package com.cordiez.mixxo.bar;

import android.util.Log;
import android.webkit.WebResourceResponse;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.security.MessageDigest;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Guarda la página, las fotos y la última lista.
 * Con red baja la versión nueva. Sin red sirve la copia del disco.
 */
class LocalStore {
    private static final String TAG = "MixxoOffline";
    private static final int MAX_BYTES = 8 * 1024 * 1024;
    private static final int FRESH_MS = 60000;

    private static final Pattern IMG = Pattern.compile("/img/[A-Za-z0-9._/-]+");
    private static final Pattern FAVICON = Pattern.compile("/favicon\\.svg");
    private static final Pattern ABS_IMG = Pattern.compile(
            "https?://[^\\s\"'<>]+?\\.(?:png|jpe?g|webp|gif|svg)(?:\\?[^\\s\"'<>]*)?");

    private final File dir;
    private final String origin;
    private final String originHost;
    private final File offlineFlag;
    private final File statusFile;
    private String userAgent = "MixxoBar";

    LocalStore(File dir, String pageUrl, File offlineFlag, File statusFile) {
        this.dir = dir;
        this.offlineFlag = offlineFlag;
        this.statusFile = statusFile;
        if (!dir.isDirectory() && !dir.mkdirs()) {
            Log.w(TAG, "sin carpeta de copia");
        }
        String parsedOrigin = null;
        String parsedHost = null;
        if (pageUrl != null) {
            try {
                URI uri = URI.create(pageUrl);
                if (uri.getHost() != null && uri.getScheme() != null) {
                    parsedHost = uri.getHost();
                    String port = uri.getPort() > 0 ? ":" + uri.getPort() : "";
                    parsedOrigin = uri.getScheme() + "://" + parsedHost + port;
                }
            } catch (IllegalArgumentException ignored) {
                parsedOrigin = null;
            }
        }
        origin = parsedOrigin;
        originHost = parsedHost;
    }

    void setUserAgent(String agent) {
        if (agent != null && agent.length() > 0) {
            userAgent = agent;
        }
    }

    WebResourceResponse respond(String url) {
        if (!handles(url)) {
            return null;
        }
        Kept kept = take(url);
        if (kept == null) {
            return forcedOffline() ? miss() : null;
        }
        if (kept.prefetch && kept.body != null) {
            new Thread(new PrefetchJob(this, kept.body)).start();
        }
        return kept.response;
    }

    private synchronized Kept take(String url) {
        File body = bodyFile(url);
        File type = typeFile(url);
        boolean cached = body.isFile() && body.length() > 0 && type.isFile();
        boolean live = url.indexOf("firestore.googleapis.com") >= 0;
        if (forcedOffline()) {
            return cached ? readCache(url, body, type, false) : null;
        }
        if (cached && !live && System.currentTimeMillis() - body.lastModified() < FRESH_MS) {
            return readCache(url, body, type, false);
        }
        Kept fresh = download(url);
        if (fresh != null) {
            return fresh;
        }
        if (cached) {
            return readCache(url, body, type, false);
        }
        return null;
    }

    private Kept download(String url) {
        HttpURLConnection conn = null;
        try {
            conn = (HttpURLConnection) new URL(url).openConnection();
            conn.setConnectTimeout(cached(url) ? 2500 : 8000);
            conn.setReadTimeout(12000);
            conn.setInstanceFollowRedirects(true);
            conn.setUseCaches(false);
            conn.setRequestProperty("User-Agent", userAgent);
            conn.setRequestProperty("Accept", "*/*");
            int code = conn.getResponseCode();
            if (code < 200 || code >= 300) {
                Log.i(TAG, "sin red " + label(url) + " " + code);
                return null;
            }
            byte[] bytes = readLimited(conn.getInputStream());
            if (bytes == null || bytes.length == 0) {
                return null;
            }
            String mime = cleanMime(conn.getContentType(), url);
            write(url, bytes, mime);
            Log.i(TAG, "red " + label(url) + " " + bytes.length);
            Kept kept = pack(bytes, mime, true);
            note(url, mime, "red");
            return kept;
        } catch (IOException error) {
            Log.i(TAG, "sin red " + label(url));
            return null;
        } finally {
            if (conn != null) {
                conn.disconnect();
            }
        }
    }

    private Kept readCache(String url, File body, File type, boolean prefetch) {
        try {
            byte[] bytes = readFile(body);
            String mime = readText(type).trim();
            if (mime.length() == 0) {
                mime = cleanMime(null, url);
            }
            Log.i(TAG, "disco " + label(url) + " " + bytes.length);
            note(url, mime, "disco");
            return pack(bytes, mime, prefetch);
        } catch (IOException error) {
            return null;
        }
    }

    private Kept pack(byte[] bytes, String mime, boolean fromNetwork) {
        String encoding = encodingFor(mime);
        Map<String, String> headers = new HashMap<String, String>();
        headers.put("Access-Control-Allow-Origin", "*");
        headers.put("Cache-Control", "no-store");
        if (encoding != null) {
            headers.put("Content-Type", mime + "; charset=" + encoding);
        } else {
            headers.put("Content-Type", mime);
        }
        Kept kept = new Kept();
        kept.body = bytes;
        kept.prefetch = fromNetwork && textual(mime);
        kept.response = new WebResourceResponse(
                mime,
                encoding,
                200,
                "OK",
                headers,
                new ByteArrayInputStream(bytes));
        return kept;
    }

    private WebResourceResponse miss() {
        Map<String, String> headers = new HashMap<String, String>();
        headers.put("Cache-Control", "no-store");
        return new WebResourceResponse(
                "text/plain",
                "UTF-8",
                503,
                "Offline",
                headers,
                new ByteArrayInputStream(new byte[0]));
    }

    String[] linkedFiles(byte[] body) {
        String text;
        try {
            text = new String(body, "UTF-8");
        } catch (IOException error) {
            return new String[0];
        }
        if (origin == null) {
            return new String[0];
        }
        LinkedHashSet<String> found = new LinkedHashSet<String>();
        collectPaths(text, IMG, found);
        collectPaths(text, FAVICON, found);
        Matcher absolute = ABS_IMG.matcher(text);
        while (absolute.find() && found.size() < 48) {
            found.add(absolute.group());
        }
        return found.toArray(new String[found.size()]);
    }

    private void collectPaths(String text, Pattern pattern, LinkedHashSet<String> found) {
        Matcher matcher = pattern.matcher(text);
        while (matcher.find() && found.size() < 48) {
            found.add(origin + matcher.group());
        }
    }

    private boolean handles(String url) {
        if (url == null) {
            return false;
        }
        if (!url.startsWith("http://") && !url.startsWith("https://")) {
            return false;
        }
        String host = hostOf(url);
        if (host == null) {
            return false;
        }
        if ("firestore.googleapis.com".equalsIgnoreCase(host)) {
            return true;
        }
        if ("arcordiezb2c.vtexassets.com".equalsIgnoreCase(host)) {
            return true;
        }
        return originHost != null && originHost.equalsIgnoreCase(host);
    }

    private boolean forcedOffline() {
        return offlineFlag != null && offlineFlag.isFile();
    }

    private boolean cached(String url) {
        File body = bodyFile(url);
        return body.isFile() && body.length() > 0;
    }

    private void write(String url, byte[] bytes, String mime) throws IOException {
        writeBytes(bodyFile(url), bytes);
        writeBytes(typeFile(url), mime.getBytes("UTF-8"));
    }

    private void writeBytes(File dest, byte[] bytes) throws IOException {
        File tmp = new File(dest.getParentFile(), dest.getName() + ".tmp");
        FileOutputStream out = new FileOutputStream(tmp);
        try {
            out.write(bytes);
        } finally {
            out.close();
        }
        if (dest.exists() && !dest.delete()) {
            tmp.delete();
            throw new IOException("no pude reemplazar " + dest.getName());
        }
        if (!tmp.renameTo(dest)) {
            tmp.delete();
            throw new IOException("no pude guardar " + dest.getName());
        }
    }

    private void note(String url, String mime, String source) {
        if (statusFile == null) {
            return;
        }
        try {
            String line = source + " " + mime + " " + label(url) + "\n";
            FileOutputStream out = new FileOutputStream(statusFile, true);
            try {
                out.write(line.getBytes("UTF-8"));
            } finally {
                out.close();
            }
        } catch (IOException ignored) {
        }
    }

    private static String label(String url) {
        if (url.indexOf("firestore.googleapis.com") >= 0) {
            return "firebase live";
        }
        return url;
    }

    private File bodyFile(String url) {
        return new File(dir, hash(url) + ".body");
    }

    private File typeFile(String url) {
        return new File(dir, hash(url) + ".type");
    }

    private static String hash(String url) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] raw = digest.digest(url.getBytes("UTF-8"));
            StringBuilder hex = new StringBuilder(raw.length * 2);
            for (int i = 0; i < raw.length; i++) {
                hex.append(String.format("%02x", raw[i] & 0xff));
            }
            return hex.toString();
        } catch (Exception error) {
            return Integer.toHexString(url.hashCode());
        }
    }

    private static String hostOf(String url) {
        try {
            return URI.create(url).getHost();
        } catch (IllegalArgumentException error) {
            return null;
        }
    }

    private static byte[] readLimited(InputStream in) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int total = 0;
        int n;
        while ((n = in.read(buf)) >= 0) {
            total += n;
            if (total > MAX_BYTES) {
                return null;
            }
            out.write(buf, 0, n);
        }
        return out.toByteArray();
    }

    private static byte[] readFile(File file) throws IOException {
        FileInputStream in = new FileInputStream(file);
        try {
            return readLimited(in);
        } finally {
            in.close();
        }
    }

    private static String readText(File file) throws IOException {
        return new String(readFile(file), "UTF-8");
    }

    private static boolean textual(String mime) {
        return mime.startsWith("text/") || "application/json".equals(mime) || "image/svg+xml".equals(mime);
    }

    private static String encodingFor(String mime) {
        if (textual(mime) || mime.startsWith("application/javascript") || mime.startsWith("text/")) {
            return "UTF-8";
        }
        return null;
    }

    private static String cleanMime(String header, String url) {
        if (header != null) {
            int cut = header.indexOf(';');
            String mime = (cut >= 0 ? header.substring(0, cut) : header).trim().toLowerCase();
            if (mime.indexOf('/') > 0) {
                return mime;
            }
        }
        String path = url;
        int query = path.indexOf('?');
        if (query >= 0) {
            path = path.substring(0, query);
        }
        path = path.toLowerCase();
        if (path.endsWith(".png")) return "image/png";
        if (path.endsWith(".jpg") || path.endsWith(".jpeg")) return "image/jpeg";
        if (path.endsWith(".svg")) return "image/svg+xml";
        if (path.endsWith(".webp")) return "image/webp";
        if (path.endsWith(".gif")) return "image/gif";
        if (path.endsWith(".js")) return "application/javascript";
        if (path.endsWith(".css")) return "text/css";
        if (path.indexOf("firestore.googleapis.com") >= 0) return "application/json";
        return "text/html";
    }
}

class Kept {
    WebResourceResponse response;
    byte[] body;
    boolean prefetch;
}

class PrefetchJob implements Runnable {
    private final LocalStore store;
    private final byte[] body;

    PrefetchJob(LocalStore store, byte[] body) {
        this.store = store;
        this.body = body;
    }

    @Override
    public void run() {
        String[] urls = store.linkedFiles(body);
        for (int i = 0; i < urls.length; i++) {
            store.respond(urls[i]);
        }
    }
}
