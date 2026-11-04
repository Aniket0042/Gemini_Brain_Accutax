import { X } from 'lucide-react';
export function TenantLoginModal({tenants=[],currentTenant,onSelectTenant,onClose}){
 return <div className="modal-overlay" onClick={onClose}><section className="modal-content" onClick={event=>event.stopPropagation()}>
 <button onClick={onClose} aria-label="Close company selection"><X/></button><h2>Choose a company</h2>
 {tenants.map(tenant=><button key={tenant.id} onClick={()=>{onSelectTenant({...tenant,organization_id:tenant.id});onClose();}} style={{display:'block',padding:16,width:'100%',textAlign:'left',fontWeight:Number(currentTenant?.organization_id)===Number(tenant.id)?700:400}}>{tenant.display_name||tenant.name}</button>)}
 {!tenants.length&&<p>No companies are available for AI access.</p>}</section></div>;
}
