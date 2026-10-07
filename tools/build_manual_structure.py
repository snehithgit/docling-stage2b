"""Rebuild derived manual maps without OCR, verification or source edits."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.manual_structure import rebuild_structure

def main():
 p=argparse.ArgumentParser();p.add_argument('processed_dir',type=Path);p.add_argument('--pdf-dir',type=Path)
 args=p.parse_args();reports=[]
 for index in sorted(args.processed_dir.glob('*/retrieval_index.jsonl')):
  manifest=index.parent/'source_manifest.json';data=json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else {}
  pdf=args.pdf_dir/Path(data.get('source_filename','')).name if args.pdf_dir and data.get('source_filename') else None
  if pdf and pdf.suffix.lower()!='.pdf':pdf=None
  report=rebuild_structure(index.parent,pdf_path=pdf)
  reports.append({'manual':index.parent.name,**report['diagnostics']})
 print(json.dumps({'books':len(reports),'model_calls':0,'original_content_changed':False,'reports':reports},indent=2))

if __name__=='__main__':main()
