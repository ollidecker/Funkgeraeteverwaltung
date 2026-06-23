(function(){const saved=localStorage.getItem('theme');if(saved==='dark'||(!saved&&window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches)){document.documentElement.classList.add('dark')}})();
function toggleTheme(){document.documentElement.classList.toggle('dark');localStorage.setItem('theme',document.documentElement.classList.contains('dark')?'dark':'light')}
const canvas=document.getElementById('sig');let drawing=false;if(canvas){const ctx=canvas.getContext('2d');ctx.lineWidth=3;ctx.lineCap='round';function pos(e){const r=canvas.getBoundingClientRect();const t=e.touches?e.touches[0]:e;return{x:(t.clientX-r.left)*(canvas.width/r.width),y:(t.clientY-r.top)*(canvas.height/r.height)}}function start(e){drawing=true;const p=pos(e);ctx.beginPath();ctx.moveTo(p.x,p.y);e.preventDefault()}function move(e){if(!drawing)return;const p=pos(e);ctx.lineTo(p.x,p.y);ctx.stroke();e.preventDefault()}function end(){drawing=false}canvas.addEventListener('mousedown',start);canvas.addEventListener('mousemove',move);window.addEventListener('mouseup',end);canvas.addEventListener('touchstart',start);canvas.addEventListener('touchmove',move);canvas.addEventListener('touchend',end)}
function saveSignature(){const c=document.getElementById('sig');const i=document.getElementById('signature_data');if(c&&i)i.value=c.toDataURL('image/png')}
function clearSig(){const c=document.getElementById('sig');if(c)c.getContext('2d').clearRect(0,0,c.width,c.height)}
function validateLoanForm(form){
  const c=form.querySelector('.extra-battery-check');
  const n=form.querySelector('.extra-battery-no');
  if(c&&c.checked&&n&&!n.value.trim()){
    alert('Bitte Spare Batterie Seriennummer angeben.');
    n.focus();
    return false;
  }
  const serialFields=[...form.querySelectorAll('input[name="radio_no"], input[name="battery_no"], input[name="extra_battery_no"]')]
    .filter(el=>el.value.trim());
  const seen={};
  for(const el of serialFields){
    const key=el.value.trim().replace(/\s+/g,'').toLowerCase();
    if(seen[key]){
      alert('Diese Seriennummer wurde in der Eingabe doppelt verwendet: '+el.value.trim());
      el.focus();
      return false;
    }
    seen[key]=true;
  }
  saveSignature();
  return true;
}
function filterLoans(){const input=document.getElementById('projectSearch');const q=(input?.value||'').toLowerCase().trim();document.querySelectorAll('#loanGrid .loan').forEach(el=>{const hay=(el.dataset.search||el.textContent||'').toLowerCase();el.style.display=hay.includes(q)?'block':'none';});}
