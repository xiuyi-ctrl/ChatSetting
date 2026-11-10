package com.xiuyi.chatsetting;

import android.util.Base64;
import org.json.*;
import java.io.*;
import java.net.*;
import java.util.regex.*;
import java.util.*;
import java.util.function.BiConsumer;

final class ShareParser {
    private static final Pattern URL = Pattern.compile("https://chat\\.deepseek\\.com/share/([a-zA-Z0-9]+)/?");
    private static final Pattern CHATGPT_URL = Pattern.compile("https://chatgpt\\.com/share/([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})/?");
    static JSONObject parse(String url,BiConsumer<Integer,String> progress) throws Exception {
        if(CHATGPT_URL.matcher(url.trim()).matches()) return parseChatGpt(url.trim(),progress);
        Matcher match=URL.matcher(url.trim());
        if(!match.matches()) throw new IllegalArgumentException("请提供公开的 ChatGPT 或 DeepSeek 分享链接");
        String id=match.group(1);
        progress.accept(5,"正在读取 DeepSeek 分享内容…");
        JSONObject payload=new JSONObject(new String(get("https://chat.deepseek.com/api/v0/share/content?share_id="+id,"https://chat.deepseek.com/share/"+id),"UTF-8"));
        JSONObject data=payload.optJSONObject("data");
        if(data==null || data.optInt("biz_code",-1)!=0) throw new IllegalArgumentException("DeepSeek 未返回这条分享记录，请检查链接是否仍公开有效");
        JSONObject shared=data.optJSONObject("biz_data");
        if(shared==null) throw new IllegalArgumentException("分享记录内容为空");
        progress.accept(20,"分享内容已读取，正在解析…");
        JSONArray messages=shared.optJSONArray("messages"), turns=new JSONArray(), warnings=new JSONArray(); int imageCount=0, attachmentBytes=0;
        if(messages!=null) for(int i=0;i<messages.length();i++) {
            if(i%5==0 || i==messages.length()-1) progress.accept(20+75*(i+1)/messages.length(),"正在处理消息 "+(i+1)+" / "+messages.length());
            JSONObject message=messages.optJSONObject(i); if(message==null) continue;
            JSONArray fragments=message.optJSONArray("fragments"), blocks=new JSONArray();
            Map<String,String> references=new HashMap<>();
            if(fragments!=null) for(int j=0;j<fragments.length();j++) {
                JSONObject search=fragments.optJSONObject(j);
                if(search==null || !search.optString("type").equals("SEARCH")) continue;
                JSONArray results=search.optJSONArray("results");
                if(results!=null) for(int k=0;k<results.length();k++) {
                    JSONObject item=results.optJSONObject(k); if(item==null) continue;
                    String refUrl=item.optString("url");
                    if(item.has("cite_index") && (refUrl.startsWith("https://") || refUrl.startsWith("http://")))
                        references.put(item.optString("cite_index"),refUrl.replace("<","%3C").replace(">","%3E").replace(" ","%20"));
                }
            }
            if(fragments!=null) for(int j=0;j<fragments.length();j++) {
                JSONObject fragment=fragments.optJSONObject(j); if(fragment==null) continue;
                String type=fragment.optString("type");
                if((type.equals("REQUEST") || type.equals("RESPONSE")) && !fragment.optString("content").isEmpty()) blocks.put(block("text",linkCitations(fragment.optString("content"),references)));
                else if(type.equals("FILE")) {
                    JSONArray files=fragment.optJSONArray("files");
                    if(files!=null) for(int k=0;k<files.length();k++) {
                        JSONObject file=files.optJSONObject(k); if(file==null) continue;
                        String path=file.optString("signed_path"), name=file.optString("file_name","未命名文件");
                        if(file.optBoolean("is_image") && path.startsWith("/file?")) {
                            try {
                                byte[] bytes=get("https://files.deepseeksvc.com/api"+path+"&ty=p",null);
                                if(bytes.length>10_000_000) throw new IOException("图片过大");
                                String mime=imageMime(bytes);
                                blocks.put(block("image","data:"+mime+";base64,"+Base64.encodeToString(bytes,Base64.NO_WRAP))); imageCount++; continue;
                            } catch(Exception ex) { warnings.put("图片 "+name+" 未能下载"); }
                        }
                        JSONObject attachment=new JSONObject().put("type","attachment").put("value",name).put("size",file.optLong("file_size")).put("url",url);
                        if(path.startsWith("/file?") && file.optLong("file_size")>0 && file.optLong("file_size")<=10_000_000 && attachmentBytes+file.optLong("file_size")<=50_000_000) {
                            try {
                                byte[] bytes=get("https://files.deepseeksvc.com/api"+path+"&ty=r",null);
                                if(bytes.length>10_000_000 || attachmentBytes+bytes.length>50_000_000) throw new IOException("文件过大");
                                attachment.put("data",Base64.encodeToString(bytes,Base64.NO_WRAP));
                                attachmentBytes+=bytes.length;
                                if(bytes.length<=1_000_000 && (name.toLowerCase().endsWith(".md") || name.toLowerCase().endsWith(".txt")))
                                    attachment.put("preview",new String(bytes,"UTF-8"));
                            } catch(Exception ex) { warnings.put("附件 "+name+" 未能下载"); }
                        }
                        blocks.put(attachment);
                    }
                }
            }
            String role=message.optString("role");
            if(role.equals("USER")) turns.put(new JSONObject().put("question",blocks).put("answer",new JSONArray()).put("warnings",new JSONArray()).put("line",turns.length()+1));
            else if(role.equals("ASSISTANT") && turns.length()>0) {
                JSONArray answer=turns.getJSONObject(turns.length()-1).getJSONArray("answer");
                for(int k=0;k<blocks.length();k++) answer.put(blocks.get(k));
            }
        }
        if(turns.length()==0) throw new IllegalArgumentException("链接已读取，但没有识别到问答消息");
        for(int i=0;i<turns.length();i++) if(turns.getJSONObject(i).getJSONArray("answer").length()==0) { turns.getJSONObject(i).getJSONArray("warnings").put("缺少回答"); warnings.put("第 "+(i+1)+" 轮：缺少回答"); }
        return new JSONObject().put("title",shared.optString("title","DeepSeek 对话")).put("filename","DeepSeek · "+id).put("type","link").put("platform","DeepSeek").put("sourceUrl",url).put("turns",turns).put("warnings",warnings).put("imageCount",imageCount);
    }
    private static JSONObject parseChatGpt(String url,BiConsumer<Integer,String> progress) throws Exception {
        Matcher match=CHATGPT_URL.matcher(url); match.matches(); String id=match.group(1);
        JSONObject shared;
        progress.accept(5,"正在读取 ChatGPT 分享内容…");
        try { shared=new JSONObject(new String(get("https://chatgpt.com/backend-api/share/"+id,url),"UTF-8")); }
        catch(Exception ex) { throw new IllegalArgumentException("ChatGPT 分享内容读取失败；请确认链接公开有效，或稍后重试",ex); }
        progress.accept(20,"分享内容已读取，正在解析…");
        JSONArray nodes=shared.optJSONArray("linear_conversation"), turns=new JSONArray(), warnings=new JSONArray();
        String conversationId=shared.optString("backing_conversation_id");
        int imageCount=0;
        if(nodes!=null) for(int i=0;i<nodes.length();i++) {
            if(i%5==0 || i==nodes.length()-1) progress.accept(20+75*(i+1)/nodes.length(),"正在处理消息 "+(i+1)+" / "+nodes.length());
            JSONObject node=nodes.optJSONObject(i), message=node==null?null:node.optJSONObject("message");
            if(message==null) continue;
            JSONObject author=message.optJSONObject("author"), content=message.optJSONObject("content");
            String role=author==null?"":author.optString("role"), type=content==null?"":content.optString("content_type");
            if((!role.equals("user") && !role.equals("assistant")) || (!type.equals("text") && !type.equals("multimodal_text"))) continue;
            if(role.equals("assistant") && Boolean.FALSE.equals(message.opt("end_turn"))) continue;
            JSONArray parts=content.optJSONArray("parts"), blocks=new JSONArray();
            if(parts!=null) for(int j=0;j<parts.length();j++) {
                Object part=parts.opt(j);
                if(part instanceof String && !((String)part).trim().isEmpty()) blocks.put(block("text",(String)part));
                else if(part instanceof JSONObject) {
                    JSONObject item=(JSONObject)part;
                    if(!item.optString("text").trim().isEmpty()) blocks.put(block("text",item.optString("text")));
                    else if(item.optString("content_type").equals("image_asset_pointer")) {
                        String pointer=item.optString("asset_pointer");
                        String assetId=pointer.contains("://")?pointer.substring(pointer.indexOf("://")+3).split("\\?")[0]:"";
                        try {
                            if(assetId.isEmpty()) throw new IOException("缺少图片 ID");
                            progress.accept(20+75*(i+1)/nodes.length(),"正在获取图片 "+(imageCount+1)+"…");
                            String base="https://chatgpt.com/backend-api/files/download/"+URLEncoder.encode(assetId,"UTF-8");
                            byte[] bytes=null;
                            if(!conversationId.isEmpty()) try {
                                bytes=get(base+"?conversation_id="+URLEncoder.encode(conversationId,"UTF-8")+"&inline=false",url);
                                if(bytes.length>8_000_000) throw new IOException("图片过大");
                                imageMime(bytes);
                            } catch(Exception ignored) { bytes=null; }
                            if(bytes==null) bytes=get(base+"?shared_conversation_id="+id+"&inline=false",url);
                            if(bytes.length>8_000_000) throw new IOException("图片过大");
                            blocks.put(block("image","data:"+imageMime(bytes)+";base64,"+Base64.encodeToString(bytes,Base64.NO_WRAP)));
                            imageCount++;
                        } catch(Exception ex) { blocks.put(block("missing-image",url)); }
                    }
                }
            }
            if(role.equals("user")) turns.put(new JSONObject().put("question",blocks).put("answer",new JSONArray()).put("warnings",new JSONArray()).put("line",turns.length()+1));
            else if(turns.length()>0) {
                JSONArray answer=turns.getJSONObject(turns.length()-1).getJSONArray("answer");
                if(blocks.length()==1 && blocks.getJSONObject(0).optString("type").equals("text") && blocks.getJSONObject(0).optString("value").length()>200) {
                    String text=blocks.getJSONObject(0).optString("value");
                    boolean repeated=false;
                    for(int j=0;j<answer.length();j++) if(answer.getJSONObject(j).optString("type").equals("text") && answer.getJSONObject(j).optString("value").contains(text)) repeated=true;
                    if(repeated) continue;
                }
                for(int j=0;j<blocks.length();j++) answer.put(blocks.get(j));
            }
        }
        if(turns.length()==0) throw new IllegalArgumentException("链接已读取，但没有识别到用户提问；该分享可能仅包含单条回答");
        for(int i=0;i<turns.length();i++) {
            JSONObject turn=turns.getJSONObject(i);
            boolean missingImage=false;
            for(String side:new String[]{"question","answer"}) {
                JSONArray blocks=turn.getJSONArray(side);
                for(int j=0;j<blocks.length();j++) if(blocks.getJSONObject(j).optString("type").equals("missing-image")) missingImage=true;
            }
            if(missingImage) { turn.getJSONArray("warnings").put("图片需在原分享页查看"); warnings.put("第 "+(i+1)+" 轮：图片需在原分享页查看"); }
            if(turn.getJSONArray("answer").length()==0) { turn.getJSONArray("warnings").put("缺少回答"); warnings.put("第 "+(i+1)+" 轮：缺少回答"); }
        }
        return new JSONObject().put("title",shared.optString("title","ChatGPT 对话")).put("filename","ChatGPT · "+id).put("type","link").put("platform","ChatGPT").put("sourceUrl",url).put("turns",turns).put("warnings",warnings).put("imageCount",imageCount);
    }
    private static JSONObject block(String type,String value) throws Exception { return new JSONObject().put("type",type).put("value",value); }
    private static String linkCitations(String content,Map<String,String> references) {
        Matcher matcher=Pattern.compile("\\[citation:(\\d+)\\]").matcher(content);
        StringBuffer result=new StringBuffer();
        while(matcher.find()) {
            String url=references.get(matcher.group(1));
            matcher.appendReplacement(result,Matcher.quoteReplacement(url==null?matcher.group(0):"["+matcher.group(1)+"](<"+url+">)"));
        }
        matcher.appendTail(result);
        return result.toString();
    }
    private static String imageMime(byte[] bytes) throws IOException {
        if(bytes.length>8 && (bytes[0]&255)==137 && bytes[1]==80 && bytes[2]==78 && bytes[3]==71) return "image/png";
        if(bytes.length>3 && (bytes[0]&255)==255 && (bytes[1]&255)==216) return "image/jpeg";
        if(bytes.length>12 && bytes[0]==82 && bytes[1]==73 && bytes[2]==70 && bytes[3]==70) return "image/webp";
        if(bytes.length>6 && bytes[0]==71 && bytes[1]==73 && bytes[2]==70) return "image/gif";
        throw new IOException("图片格式不支持");
    }
    private static byte[] get(String url,String referer) throws Exception {
        HttpURLConnection c=(HttpURLConnection)new URL(url).openConnection();
        c.setConnectTimeout(15000); c.setReadTimeout(15000);
        c.setRequestProperty("User-Agent","Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/131.0.0.0 Mobile Safari/537.36");
        c.setRequestProperty("Accept",url.contains("/files/download/")?"image/*":"application/json"); c.setRequestProperty("Accept-Language","zh-CN,zh;q=0.9");
        if(referer!=null) c.setRequestProperty("Referer",referer);
        try {
            if(c.getResponseCode()!=200) throw new IOException("HTTP "+c.getResponseCode());
            try(InputStream in=c.getInputStream(); ByteArrayOutputStream out=new ByteArrayOutputStream()) { byte[] b=new byte[8192]; int n; while((n=in.read(b))!=-1) out.write(b,0,n); return out.toByteArray(); }
        } finally { c.disconnect(); }
    }
}
