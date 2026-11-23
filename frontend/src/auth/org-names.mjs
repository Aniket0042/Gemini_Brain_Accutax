// The AI service can only invent "Organization 6" when it cannot read company names itself.
// The browser session (/auth/session) always carries the real names, so use those in their place.
const PLACEHOLDER=/^\s*organi[sz]ation\s*#?\d*\s*$/i;
const idOf=item=>Number(item?.id??item?.organization_id);
export const isPlaceholderName=name=>typeof name!=='string'||!name.trim()||PLACEHOLDER.test(name);
export function applySessionOrgNames(tenants,sessionOrganizations){
  const real=new Map();
  for(const org of sessionOrganizations||[]){
    const id=idOf(org);
    if(Number.isSafeInteger(id)&&typeof org?.name==='string'&&org.name.trim())real.set(id,org.name.trim());
  }
  return (tenants||[]).map(tenant=>{
    const name=real.get(idOf(tenant));
    if(!name)return tenant;
    const current=tenant.name??tenant.organization_name??tenant.company_name;
    if(!isPlaceholderName(current))return tenant;
    return{...tenant,name,organization_name:name};
  });
}
