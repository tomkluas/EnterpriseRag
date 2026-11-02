# 向本地双后端上传最小 Office/PDF 合成样本并检索指定事实；依赖 Docker 镜像检查与平台 API，不覆盖扫描件或复杂排版。
"""Synthetic DOCX/XLSX/text-PDF parser probes; no office libraries or business files."""
import argparse,asyncio,io,json,os,uuid,zipfile,subprocess
from pathlib import Path
from dotenv import dotenv_values
import httpx
from app.v3.knowledge.registry import BackendRegistry
from app.v3.knowledge.contracts import Binding,Scope
from types import SimpleNamespace


# 在内存中打包最小 Office 容器，避免引入办公软件依赖或现有业务文件。
def package(files):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,data in files.items():archive.writestr(name,data)
    return output.getvalue()


# 为不同格式放入同一个合成事实，便于检验解析后是否仍可召回；最小样本不证明复杂格式兼容。
def fixtures(marker):
    text=f'SYNTHETIC {marker}. Travel allowance policy is 317 yuan per day.'
    rel='<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="{target}"/></Relationships>'
    types='<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>{overrides}</Types>'
    doc=package({'[Content_Types].xml':types.format(overrides='<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'),
        '_rels/.rels':rel.format(target='word/document.xml'),'word/document.xml':f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>'})
    sheet=package({'[Content_Types].xml':types.format(overrides='<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'),
        '_rels/.rels':rel.format(target='xl/workbook.xml'),'xl/workbook.xml':'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Policy" sheetId="1" r:id="rId1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        'xl/worksheets/sheet1.xml':f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Policy</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>{text}</t></is></c></row></sheetData></worksheet>'})
    stream=f'BT /F1 12 Tf 40 700 Td ({text}) Tj ET'.encode()
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream']
    pdf=b'%PDF-1.4\n';offsets=[0]
    for i,obj in enumerate(objects,1):offsets.append(len(pdf));pdf+=f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n'
    xref=len(pdf);pdf+=b'xref\n0 6\n0000000000 65535 f \n'+b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:])+f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode()
    return {'.docx':doc,'.xlsx':sheet,'.pdf':pdf}


# 等待解析完成且有实际分块后检索 317 这一事实，并绑定端点、集合、镜像和模型，避免证明被误用到别的环境。
async def main(args):
    config=dotenv_values(args.env_file)
    for key,value in config.items():
        if value is not None:os.environ[key]=value
    marker='FORMAT_'+uuid.uuid4().hex;checks=[]
    async with httpx.AsyncClient() as client:
        registry=BackendRegistry(client)
        for provider,prefix in [('weknora','WK'),('dify','DIFY')]:
            instance=SimpleNamespace(provider=provider,base_url=config['V3_'+prefix+'_BASE_URL'],key_ref='V3_SECRET_'+prefix+'_READ',write_key_ref='V3_SECRET_'+prefix+'_WRITE',manage_key_ref='V3_SECRET_'+prefix+'_READ',collection_config={'collection_id':config['V3_'+prefix+'_COLLECTION_ID']})
            writer=registry.get(instance,'write');collection=await writer.prepare_collection('probe');reader=registry.get(instance)
            if instance.base_url!={'weknora':'http://127.0.0.1:18080','dify':'http://127.0.0.1:18082'}[provider]:raise RuntimeError('Probe attestation requires the configured local deployment')
            container={'weknora':'WeKnora-app','dify':'enterpriserag-v3-dify-g0-api-1'}[provider]
            image=subprocess.run(['docker','inspect',container,'--format','{{.Image}}'],capture_output=True,text=True,check=True).stdout.strip()
            for extension,content in fixtures(marker).items():
                try:
                    submitted=await writer.submit(collection['id'],marker+extension,content,marker+extension)
                    binding=Binding('formats',marker,marker,marker,provider,collection['id'],submitted.remote_id,marker+extension,submitted.batch_id)
                    async with asyncio.timeout(150):
                        while True:
                            status=await reader.status(binding)
                            if status.state=='failed':raise RuntimeError('parse_failed')
                            if status.state=='completed':break
                            await asyncio.sleep(2)
                    if not status.chunks:raise RuntimeError('no_chunks')
                    if provider=='dify':await writer.set_binding_metadata(binding,collection['metadata_id'])
                    evidence=await reader.retrieve('Travel allowance policy 317 yuan',Scope('formats',(binding,)))
                    if not evidence or not any('317' in item.content for item in evidence):raise RuntimeError('fact_not_retrieved')
                    row={'provider':provider,'extension':extension,'status':'passed','chunks':status.chunks,'fact_preserved':True}
                except Exception as error:row={'provider':provider,'extension':extension,'status':'failed','error_type':type(error).__name__,'reason':str(error) if type(error) is RuntimeError else getattr(error,'code','upstream_failure')}
                row.update(endpoint=instance.base_url,collection_id=collection['id'],image_digest=image,embedding_model=config['V3_EMBEDDING_MODEL'])
                checks.append(row);print(json.dumps({key:row[key] for key in ['provider','extension','status']}),flush=True)
    Path(args.output).write_text(json.dumps({'checks':checks,'scope':'synthetic minimal files only','not_verified':['scanned PDF OCR','cross-page tables','complex office layouts','production format qualification']},ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--env-file',required=True);parser.add_argument('--output',required=True)
    asyncio.run(main(parser.parse_args()))
