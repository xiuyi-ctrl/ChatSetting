package com.xiuyi.chatsetting;

import android.app.Activity;
import android.os.Bundle;
import android.content.*;
import android.net.Uri;
import android.webkit.*;
import android.util.Base64;
import android.graphics.Color;
import android.graphics.Insets;
import android.os.Build;
import android.view.WindowInsets;
import android.widget.FrameLayout;
import java.io.*;
import java.util.concurrent.*;

public class MainActivity extends Activity {
    private static final String ROOT="https://appassets.androidplatform.net/assets/www/";
    private static final int FILE_PICK=100, TEMPLATE_SAVE=101;
    private WebView web;
    private ValueCallback<Uri[]> fileCallback;
    private final ExecutorService executor=Executors.newSingleThreadExecutor();

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        FrameLayout root=new FrameLayout(this);
        root.setBackgroundColor(Color.rgb(20,36,59));
        web=new WebView(this);
        root.addView(web,new FrameLayout.LayoutParams(-1,-1));
        if(Build.VERSION.SDK_INT>=35) {
            root.setOnApplyWindowInsetsListener((view,insets) -> {
                Insets bars=insets.getInsets(WindowInsets.Type.systemBars());
                view.setPadding(bars.left,bars.top,bars.right,bars.bottom);
                return insets;
            });
        }
        setContentView(root);
        if(Build.VERSION.SDK_INT>=35) root.requestApplyInsets();
        WebSettings settings=web.getSettings();
        settings.setJavaScriptEnabled(true); settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false); settings.setAllowContentAccess(true);
        web.addJavascriptInterface(new Bridge(),"AndroidBridge");
        web.setWebViewClient(new WebViewClient() {
            @Override public WebResourceResponse shouldInterceptRequest(WebView view,WebResourceRequest request) {
                String url=request.getUrl().toString();
                if(!url.startsWith(ROOT)) return null;
                String path=url.substring(ROOT.length());
                if(path.contains("..")) return null;
                try {
                    InputStream stream=getAssets().open("www/"+path);
                    String mime=path.endsWith(".html")?"text/html":path.endsWith(".js")?"text/javascript":path.endsWith(".css")?"text/css":path.endsWith(".woff2")?"font/woff2":path.endsWith(".woff")?"font/woff":path.endsWith(".ttf")?"font/ttf":path.endsWith(".docx")?"application/vnd.openxmlformats-officedocument.wordprocessingml.document":"application/octet-stream";
                    return new WebResourceResponse(mime,"UTF-8",stream);
                } catch(IOException ex) { return null; }
            }
            @Override public boolean shouldOverrideUrlLoading(WebView view,WebResourceRequest request) {
                Uri uri=request.getUrl();
                if(uri.toString().startsWith(ROOT)) return false;
                if("https".equals(uri.getScheme())) startActivity(new Intent(Intent.ACTION_VIEW,uri));
                return true;
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onShowFileChooser(WebView view,ValueCallback<Uri[]> callback,FileChooserParams params) {
                if(fileCallback!=null) fileCallback.onReceiveValue(null);
                fileCallback=callback;
                Intent intent=new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("application/vnd.openxmlformats-officedocument.wordprocessingml.document");
                intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE,true);
                startActivityForResult(intent,FILE_PICK);
                return true;
            }
        });
        web.setDownloadListener((url,userAgent,contentDisposition,mimeType,length) -> {
            if(url.endsWith("AB_conversation_template_v2.docx")) saveTemplate();
        });
        web.loadUrl(ROOT+"interface-sample.html");
    }
    private void saveTemplate() {
        Intent intent=new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("application/vnd.openxmlformats-officedocument.wordprocessingml.document");
        intent.putExtra(Intent.EXTRA_TITLE,"AB_conversation_template_v2.docx");
        startActivityForResult(intent,TEMPLATE_SAVE);
    }
    @Override protected void onActivityResult(int request,int result,Intent data) {
        super.onActivityResult(request,result,data);
        if(request==FILE_PICK) {
            if(fileCallback==null) return;
            Uri[] uris=null;
            if(result==RESULT_OK && data!=null) {
                if(data.getClipData()!=null) { int count=data.getClipData().getItemCount(); uris=new Uri[count]; for(int i=0;i<count;i++) uris[i]=data.getClipData().getItemAt(i).getUri(); }
                else if(data.getData()!=null) uris=new Uri[]{data.getData()};
            }
            fileCallback.onReceiveValue(uris); fileCallback=null;
        } else if(request==TEMPLATE_SAVE && result==RESULT_OK && data!=null && data.getData()!=null) {
            try(InputStream in=getAssets().open("www/AB_conversation_template_v2.docx"); OutputStream out=getContentResolver().openOutputStream(data.getData())) {
                byte[] bytes=new byte[8192]; int n; while((n=in.read(bytes))!=-1) out.write(bytes,0,n);
            } catch(IOException ignored) {}
        }
    }
    private void respond(int id,String value,String error) {
        runOnUiThread(() -> web.evaluateJavascript("window.nativeResolve("+id+","+org.json.JSONObject.quote(value)+","+org.json.JSONObject.quote(error)+")",null));
    }
    private final class Bridge {
        @JavascriptInterface public void saveTemplate() { runOnUiThread(() -> MainActivity.this.saveTemplate()); }
        @JavascriptInterface public void parseDocx(String encoded,String name,int id) {
            executor.execute(() -> {
                try { respond(id,DocxParser.parse(Base64.decode(encoded,Base64.DEFAULT),name).toString(),""); }
                catch(Exception ex) { respond(id,"",ex.getMessage()==null?"文档解析失败":ex.getMessage()); }
            });
        }
        @JavascriptInterface public void parseLink(String url,int id) {
            executor.execute(() -> {
                try { respond(id,ShareParser.parse(url).toString(),""); }
                catch(Exception ex) { respond(id,"",ex.getMessage()==null?"链接读取失败":ex.getMessage()); }
            });
        }
    }
    @Override public void onBackPressed() {
        web.evaluateJavascript("(() => { const app=document.querySelector('.app'); if(app.classList.contains('import-open')) { closeMobileImport(); return 'handled'; } if(app.classList.contains('doc-menu-open')) { closeMobileDocMenu(); return 'handled'; } if(app.dataset.mobileView==='read') { setMobileView('list'); return 'handled'; } return 'exit'; })()", value -> {
            if("\"exit\"".equals(value)) super.onBackPressed();
        });
    }
    @Override protected void onDestroy() { executor.shutdownNow(); web.destroy(); super.onDestroy(); }
}
