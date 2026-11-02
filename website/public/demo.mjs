// Fixed anonymous examples only. No file API, upload, storage or network requests.
const states=[
  {status:'待开发',files:0,versions:0,count:0,note:'先有项目，再收材料。一步一步，试着走完一次交付。',next:'收进示例材料 →'},
  {status:'待交付',files:3,versions:0,count:0,note:'材料复制到项目目录，原件保留。收好文件，不等于已经交付。',next:'看看版本怎么留 →'},
  {status:'待交付',files:3,versions:2,count:0,note:'ZIP 内容更新后，保存新的归档版本。同一个文件名，前后两版都在。',next:'人工确认这次交付 →'},
  {status:'已交付',files:3,versions:2,count:1,note:'归档校验成功，再记下交付。示例项目首次计 1 项，以后补交只留明细。',next:'从头再演示一次 ↻'},
];

export function demoState(index){
  if(!Number.isInteger(index)||index<0||index>=states.length)throw new RangeError('Invalid demo step');
  return {...states[index]};
}

const mounted=new WeakSet();
export function mountDemo(root){
  if(!root||mounted.has(root))return false;
  const fields=Object.fromEntries(['state','files','versions','count','status','controls','next'].map(key=>[key,root.querySelector(`[data-demo-${key}]`)]));
  const steps=[...root.querySelectorAll('[data-demo-step]')];
  if(Object.values(fields).some(value=>!value)||steps.length!==3)return false;
  let current=0;
  const render=index=>{
    const state=demoState(index);current=index;root.dataset.step=String(index);
    fields.state.textContent=state.status;
    fields.files.textContent=state.files+' 份';fields.versions.textContent=state.versions+' 版';fields.count.textContent=state.count+' 项';
    fields.status.textContent=state.note;fields.next.textContent=state.next;
    steps.forEach(button=>button.setAttribute('aria-pressed',String(Number(button.dataset.demoStep)===index)));
  };
  fields.next.addEventListener('click',()=>render((current+1)%states.length));
  steps.forEach(button=>button.addEventListener('click',()=>render(Number(button.dataset.demoStep))));
  mounted.add(root);render(0);fields.controls.hidden=false;
  return true;
}

if(typeof document!=='undefined')mountDemo(document.querySelector('[data-demo-root]'));
