import { Download, FileText, Folder } from 'lucide-react'
import type { ModelingTemplateFile } from '../../../shared/api/modelingTemplates'

export const formatBytes = (value: number) => value < 1024 ? `${value} B` : value < 1048576 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1048576).toFixed(1)} MB`
type FolderNode = { folders: Map<string, FolderNode>; files: ModelingTemplateFile[] }
export function TemplateFileTree({ files, onDownload, disabled }: { files: ModelingTemplateFile[]; onDownload: (file: ModelingTemplateFile) => void; disabled: boolean }) {
  const root: FolderNode = { folders: new Map(), files: [] }
  for (const file of files) {
    let node = root
    for (const part of file.relative_path.split('/').slice(0, -1)) {
      if (!node.folders.has(part)) node.folders.set(part, { folders: new Map(), files: [] })
      node = node.folders.get(part)!
    }
    node.files.push(file)
  }
  function render(node: FolderNode, prefix: string) {
    return <>{[...node.folders].sort(([a], [b]) => a.localeCompare(b)).map(([name, child]) => <details className="template-folder" open key={`${prefix}/${name}`}><summary><Folder size={15} />{name}</summary>{render(child, `${prefix}/${name}`)}</details>)}{node.files.map(file => <div className="file-row" key={file.id}><FileText size={16} /><span title={file.relative_path}>{file.relative_path}</span><small>{formatBytes(file.size_bytes)}</small><button disabled={disabled} aria-label={`${file.relative_path} 다운로드`} onClick={() => onDownload(file)}><Download size={16} /></button></div>)}</>
  }
  return <div className="file-list">{files.length ? render(root, '') : <p className="template-empty">저장된 CSV가 없습니다. 폴더 또는 파일을 선택해 첫 파일 버전을 저장하세요.</p>}</div>
}
