import {Info} from 'lucide-react';

export default function HelpHint({label='About this view',children}:{label?:string;children:React.ReactNode}) {
  return <details className="help-hint" onKeyDown={e=>{if(e.key==='Escape')e.currentTarget.open=false;}}>
    <summary aria-label={label} title={label}><Info size={16}/></summary>
    <div className="help-hint-content" role="note">{children}</div>
  </details>;
}
