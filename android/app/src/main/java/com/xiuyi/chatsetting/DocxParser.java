package com.xiuyi.chatsetting;

import android.util.Base64;
import org.json.JSONArray;
import org.json.JSONObject;
import org.w3c.dom.*;
import javax.xml.parsers.DocumentBuilderFactory;
import javax.xml.parsers.DocumentBuilder;
import java.io.*;
import java.util.*;
import java.util.regex.*;
import java.util.zip.*;

final class DocxParser {
    private static final String W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";
    private static final String A = "http://schemas.openxmlformats.org/drawingml/2006/main";
    private static final String R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
    private static final String P = "http://schemas.openxmlformats.org/package/2006/relationships";
    private static final Pattern ROLE = Pattern.compile("^\\s*([AB])\\s*[：:]\\s*(.*)$", Pattern.DOTALL);
    private static final Pattern LABEL = Pattern.compile("第\\s*[0-9０-９一二三四五六七八九十百]+\\s*轮");
    private static final Pattern SEPARATOR = Pattern.compile("[─━—\\-_=\\s]{5,}");

    private static final class Part {
        String type, value;
        Part(String t, String v) { type=t; value=v; }
    }
    private static final class Para {
        int line; String text, role; List<Part> parts;
        Para(int n, String t, String r, List<Part> p) { line=n; text=t; role=r; parts=p; }
    }

    static JSONObject parse(byte[] bytes, String filename) throws Exception {
        Map<String, byte[]> files = new HashMap<>();
        try (ZipInputStream zip = new ZipInputStream(new ByteArrayInputStream(bytes))) {
            ZipEntry entry;
            while ((entry = zip.getNextEntry()) != null) {
                ByteArrayOutputStream out = new ByteArrayOutputStream();
                byte[] chunk = new byte[8192]; int count;
                while ((count = zip.read(chunk)) != -1) out.write(chunk, 0, count);
                files.put(entry.getName(), out.toByteArray());
            }
        }
        if (!files.containsKey("word/document.xml")) throw new IllegalArgumentException("无法读取 Word 文档，请确认文件是有效的 .docx");
        Document doc = xml(files.get("word/document.xml"));
        Map<String,String> images = new HashMap<>();
        byte[] relationships = files.get("word/_rels/document.xml.rels");
        if (relationships != null) {
            NodeList rels = xml(relationships).getElementsByTagNameNS(P, "Relationship");
            for (int i=0;i<rels.getLength();i++) {
                Element e=(Element)rels.item(i);
                if (e.getAttribute("Type").contains("/image") && !e.hasAttribute("TargetMode")) {
                    String target=e.getAttribute("Target");
                    images.put(e.getAttribute("Id"), target.startsWith("/word/") ? target.substring(1) : "word/"+target.replaceFirst("^/", ""));
                }
            }
        }
        List<Para> paras = new ArrayList<>();
        NodeList body = doc.getElementsByTagNameNS(W,"body");
        if (body.getLength()==0) throw new IllegalArgumentException("Word 文档没有正文");
        NodeList children=body.item(0).getChildNodes(); int line=0;
        for(int i=0;i<children.getLength();i++) {
            Node p=children.item(i);
            if(!(p instanceof Element) || !W.equals(p.getNamespaceURI()) || !"p".equals(p.getLocalName())) continue;
            line++;
            List<Part> parts=new ArrayList<>(); collect(p, parts, images, files);
            StringBuilder txt=new StringBuilder(); for(Part part:parts) if(part.type.equals("text")) txt.append(part.value);
            String text=txt.toString().trim();
            if(text.isEmpty() && parts.stream().noneMatch(x -> x.type.equals("image"))) continue;
            NodeList styles=((Element)p).getElementsByTagNameNS(W,"pStyle");
            String style=styles.getLength()>0 ? ((Element)styles.item(0)).getAttributeNS(W,"val") : "";
            if(style.equals("Subtitle") && LABEL.matcher(text).matches()) continue;
            String role=style.equals("ABRole") && text.matches("[AB]\\s*[：:]") ? text.substring(0,1) : "";
            paras.add(new Para(line,text,role,parts));
        }
        boolean styled=paras.stream().anyMatch(p -> !p.role.isEmpty());
        List<List<Para>> groups=new ArrayList<>(); List<Para> group=new ArrayList<>();
        boolean separated=false;
        for(Para p:paras) {
            if(SEPARATOR.matcher(p.text).matches()) { separated=true; if(!group.isEmpty()) { groups.add(group); group=new ArrayList<>(); } }
            else group.add(p);
        }
        if(!group.isEmpty()) groups.add(group);
        if(!separated) {
            groups.clear(); group=new ArrayList<>(); boolean hasAnswer=false, hasQuestion=false;
            for(Para p:paras) {
                String role=role(p,styled);
                if(role.equals("A") && hasAnswer) { groups.add(group); group=new ArrayList<>(); hasAnswer=false; hasQuestion=false; }
                group.add(p);
                if(role.equals("A")) hasQuestion=true;
                if(role.equals("B") && hasQuestion) hasAnswer=true;
            }
            if(!group.isEmpty()) groups.add(group);
        }
        JSONArray turns=new JSONArray(), warnings=new JSONArray(); int imageCount=0;
        for(List<Para> g:groups) {
            int ai=-1,bi=-1;
            for(int i=0;i<g.size();i++) { String role=role(g.get(i),styled); if(ai<0 && role.equals("A")) ai=i; else if(ai>=0 && bi<0 && role.equals("B")) { bi=i; break; } }
            if(ai<0) continue;
            JSONArray question=blocks(g,ai,bi<0?g.size():bi), answer=bi<0?new JSONArray():blocks(g,bi,g.size());
            JSONArray issue=new JSONArray();
            if(bi<0) { issue.put("缺少回答"); warnings.put("第 "+(turns.length()+1)+" 轮：缺少回答"); }
            imageCount+=countImages(question)+countImages(answer);
            turns.put(new JSONObject().put("question",question).put("answer",answer).put("warnings",issue).put("line",g.get(ai).line));
        }
        if(turns.length()==0) throw new IllegalArgumentException("没有识别到 A：提问。请检查文档中的角色标记和分隔线。");
        String title=filename.replaceFirst("(?i)\\.docx$", "");
        for(Para p:paras) if(!p.text.isEmpty() && !ROLE.matcher(p.text).matches() && !SEPARATOR.matcher(p.text).matches()) { title=p.text; break; }
        return new JSONObject().put("title",title).put("filename",filename).put("turns",turns).put("warnings",warnings).put("imageCount",imageCount);
    }
    private static Document xml(byte[] bytes) throws Exception {
        String source=new String(bytes,"UTF-8");
        if(source.replace("\0","").toUpperCase(Locale.ROOT).contains("<!DOCTYPE")) throw new IllegalArgumentException("Word 文档包含不支持的 XML 声明");
        DocumentBuilderFactory f=DocumentBuilderFactory.newInstance(); f.setNamespaceAware(true);
        f.setExpandEntityReferences(false);
        DocumentBuilder builder=f.newDocumentBuilder();
        builder.setEntityResolver((publicId,systemId) -> { throw new IllegalArgumentException("Word 文档引用了外部 XML 实体"); });
        return builder.parse(new ByteArrayInputStream(bytes));
    }
    private static void collect(Node node,List<Part> parts,Map<String,String> images,Map<String,byte[]> files) {
        if(node instanceof Element) {
            Element e=(Element)node;
            if(W.equals(e.getNamespaceURI()) && "t".equals(e.getLocalName())) { parts.add(new Part("text",e.getTextContent())); return; }
            if(A.equals(e.getNamespaceURI()) && "blip".equals(e.getLocalName())) {
                String path=images.get(e.getAttributeNS(R,"embed")); byte[] raw=files.get(path);
                if(raw!=null) parts.add(new Part("image","data:"+(path.toLowerCase().endsWith(".png")?"image/png":"image/jpeg")+";base64,"+Base64.encodeToString(raw,Base64.NO_WRAP)));
                else parts.add(new Part("text","[图片无法提取]"));
                return;
            }
        }
        NodeList children=node.getChildNodes(); for(int i=0;i<children.getLength();i++) collect(children.item(i),parts,images,files);
    }
    private static String role(Para p,boolean styled) { if(styled) return p.role; Matcher m=ROLE.matcher(p.text); return m.matches()?m.group(1):""; }
    private static JSONArray blocks(List<Para> paras,int from,int to) throws Exception {
        JSONArray out=new JSONArray();
        for(int i=from;i<to;i++) {
            Para p=paras.get(i); int trim=0;
            if(i==from) { Matcher m=ROLE.matcher(p.text); if(m.matches()) trim=m.start(2); }
            StringBuilder text=new StringBuilder();
            for(Part part:p.parts) {
                if(part.type.equals("text")) {
                    String value=part.value;
                    if(trim>0) { int cut=Math.min(trim,value.length()); value=value.substring(cut); trim-=cut; }
                    text.append(value);
                } else { if(!text.toString().trim().isEmpty()) out.put(new JSONObject().put("type","text").put("value",text.toString().trim())); text.setLength(0); out.put(new JSONObject().put("type","image").put("value",part.value)); }
            }
            if(!text.toString().trim().isEmpty()) out.put(new JSONObject().put("type","text").put("value",text.toString().trim()));
        }
        return out;
    }
    private static int countImages(JSONArray blocks) throws Exception { int count=0; for(int i=0;i<blocks.length();i++) if(blocks.getJSONObject(i).getString("type").equals("image")) count++; return count; }
}
