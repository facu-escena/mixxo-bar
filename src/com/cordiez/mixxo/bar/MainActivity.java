package com.cordiez.mixxo.bar;

import android.app.Activity;
import android.os.Bundle;
import android.view.View;
import android.view.WindowManager;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;
import java.io.IOException;

public class MainActivity extends Activity {
    private WebView web;
    String loadedUrl;
    boolean retrying;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        hideSystemUi();

        web = new WebView(this);
        web.setBackgroundColor(0xFF141414);
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(true);
        settings.setAllowFileAccessFromFileURLs(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setUseWideViewPort(true);
        settings.setLoadWithOverviewMode(true);
        settings.setCacheMode(WebSettings.LOAD_NO_CACHE);
        File offline = new File(getFilesDir(), "offline");
        File external = getExternalFilesDir(null);
        File flag = external == null ? null : new File(external, "offline-now");
        File status = external == null ? null : new File(external, "offline-status.txt");
        LocalStore store = new LocalStore(offline, readServerUrl(), flag, status);
        store.setUserAgent(settings.getUserAgentString());
        web.setWebViewClient(new SignageClient(this, store));
        setContentView(web);
    }

    @Override
    protected void onResume() {
        super.onResume();
        hideSystemUi();
        loadPage();
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) {
            hideSystemUi();
        }
    }

    void loadPage() {
        String url = readServerUrl();
        if (url == null) {
            url = "file:///android_asset/index.html";
        }
        if (url.equals(loadedUrl)) {
            return;
        }
        loadedUrl = url;
        web.loadUrl(url);
    }

    private String readServerUrl() {
        File dir = getExternalFilesDir(null);
        if (dir == null) {
            return null;
        }
        File file = new File(dir, "server.txt");
        if (!file.isFile()) {
            return null;
        }
        BufferedReader reader = null;
        try {
            reader = new BufferedReader(new FileReader(file));
            String line = reader.readLine();
            if (line == null) {
                return null;
            }
            line = line.trim();
            if (line.startsWith("http://") || line.startsWith("https://")) {
                return line;
            }
        } catch (IOException ignored) {
            return null;
        } finally {
            if (reader != null) {
                try {
                    reader.close();
                } catch (IOException ignored) {
                }
            }
        }
        return null;
    }

    private void hideSystemUi() {
        getWindow().getDecorView().setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                        | View.SYSTEM_UI_FLAG_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_LAYOUT_STABLE);
    }
}

class SignageClient extends WebViewClient {
    private final MainActivity activity;
    private final LocalStore store;

    SignageClient(MainActivity activity, LocalStore store) {
        this.activity = activity;
        this.store = store;
    }

    @Override
    public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
        if (request == null || request.getUrl() == null) {
            return null;
        }
        String method = request.getMethod();
        if (method != null && !"GET".equalsIgnoreCase(method)) {
            return null;
        }
        return store.respond(request.getUrl().toString());
    }

    @Override
    public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
        if (request == null || !request.isForMainFrame() || activity.retrying) {
            return;
        }
        activity.retrying = true;
        view.postDelayed(new RetryLoad(activity), 3000);
    }
}

class RetryLoad implements Runnable {
    private final MainActivity activity;

    RetryLoad(MainActivity activity) {
        this.activity = activity;
    }

    @Override
    public void run() {
        activity.retrying = false;
        activity.loadedUrl = null;
        activity.loadPage();
    }
}
