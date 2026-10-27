const methodLabels = Object.freeze({ wechat: '微信', alipay: '支付宝' });
const localImage = /^\/support\/[a-zA-Z0-9][a-zA-Z0-9_-]{0,119}\.(png|jpg|jpeg|webp)$/;

export function validateSupportConfig(value) {
  const disabled = { enabled: false, methods: [] };
  if (!value || value.enabled !== true || !Array.isArray(value.methods)
      || value.methods.length < 1 || value.methods.length > 2) return disabled;
  const seen = new Set();
  const methods = [];
  for (const method of value.methods) {
    if (!method || typeof method.type !== 'string' || !Object.hasOwn(methodLabels, method.type) || seen.has(method.type)
        || typeof method.image !== 'string' || !localImage.test(method.image)) return disabled;
    seen.add(method.type);
    methods.push({ type: method.type, image: method.image });
  }
  return { enabled: true, methods };
}

function loadImage(method) {
  return new Promise((resolve) => {
    const image = new Image();
    const timer = setTimeout(() => finish(null), 8000);
    const finish = (value) => {
      clearTimeout(timer);
      image.onload = null;
      image.onerror = null;
      resolve(value);
    };
    image.onload = () => {
      const valid = image.naturalWidth >= 200 && image.naturalHeight >= 200
        && image.naturalWidth <= 4096 && image.naturalHeight <= 4096;
      finish(valid ? { method, image } : null);
    };
    image.onerror = () => finish(null);
    image.alt = `${methodLabels[method.type]}收款码`;
    image.decoding = 'async';
    image.src = method.image;
  });
}

async function initializeSupport() {
  const section = document.getElementById('support');
  const links = document.querySelectorAll('[data-support-link]');
  if (!section) return;
  section.hidden = true;
  links.forEach((link) => { link.hidden = true; });
  try {
    const response = await fetch('/support.json', { credentials: 'same-origin', cache: 'no-cache', signal: AbortSignal.timeout(8000) });
    if (!response.ok) return;
    const config = validateSupportConfig(await response.json());
    if (!config.enabled) return;
    const loaded = (await Promise.all(config.methods.map(loadImage))).filter(Boolean);
    if (loaded.length === 0) return;
    const content = document.createElement('div');
    content.className = 'wrap support-content';
    const heading = document.createElement('h2');
    heading.id = 'support-heading';
    heading.textContent = '支持工作箱';
    const intro = document.createElement('p');
    intro.className = 'support-intro';
    intro.textContent = '如果工作箱帮你省下了时间，欢迎自愿支持后续维护。是否支持都不影响免费下载和使用。';
    const details = document.createElement('details');
    const summary = document.createElement('summary');
    summary.textContent = '查看收款码';
    const cards = document.createElement('div');
    cards.className = 'support-methods';
    for (const { method, image } of loaded) {
      const card = document.createElement('figure');
      const caption = document.createElement('figcaption');
      caption.textContent = `${methodLabels[method.type]}支持`;
      card.append(image, caption);
      cards.append(card);
    }
    const note = document.createElement('p');
    note.className = 'support-note';
    note.textContent = '请用对应应用扫码，并在付款前确认收款人。金额由你自行决定。';
    details.append(summary, cards, note);
    content.append(heading, intro, details);
    section.replaceChildren(content);
    section.classList.add('support-section');
    section.setAttribute('aria-labelledby', heading.id);
    section.hidden = false;
    links.forEach((link) => { link.hidden = false; });
  } catch {
    // 配置或图片不可用时保持隐藏，不显示未配置的收款入口。
  }
}

if (typeof document !== 'undefined') initializeSupport();
