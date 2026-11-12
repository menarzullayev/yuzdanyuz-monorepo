// Transliteration of the Uzbek Latin message catalog (uz) into
//   * uz-Cyrl  - Uzbek Cyrillic (1995 orthography)
//   * uz-yangi - the new Uzbek Latin alphabet (oʻ->ö, gʻ->ğ, sh->ş, ch->ç)
// Text inside ICU braces {...} and tags <...> is never touched.

const OKINA = '\u02BB'; // ʻ  (oʻ, gʻ)
const TUTUQ = '\u02BC'; // ʼ  (tutuq belgisi: maʼno)
const isUpper = (c) => c !== c.toLowerCase();
const isLetter = (c) => !!c && /\p{L}/u.test(c);
const lower = (c) => (c ? c.toLowerCase() : '');

/** Make legacy apostrophes canonical: after o/g -> ʻ, anywhere else -> ʼ. */
export function normalizeApostrophes(s) {
  return s.replace(/([oOgG])['\u2019\u2018`]/g, `$1${OKINA}`).replace(/['\u2019\u2018`]/g, TUTUQ);
}

/** Split into [text, protected] chunks so ICU args and tags survive. */
function chunks(s) {
  const out = [];
  let buf = '';
  let i = 0;
  while (i < s.length) {
    const c = s[i];
    if (c === '{' || c === '<') {
      const close = c === '{' ? '}' : '>';
      let depth = 0;
      let j = i;
      for (; j < s.length; j++) {
        if (s[j] === c) depth++;
        else if (s[j] === close && --depth === 0) break;
      }
      if (j < s.length) {
        if (buf) out.push([buf, false]);
        buf = '';
        out.push([s.slice(i, j + 1), true]);
        i = j + 1;
        continue;
      }
    }
    buf += c;
    i++;
  }
  if (buf) out.push([buf, false]);
  return out;
}

function mapChunks(s, fn) {
  return chunks(normalizeApostrophes(s))
    .map(([t, keep]) => (keep ? t : fn(t)))
    .join('');
}

const up = (ch, isUp) => (isUp ? ch.toUpperCase() : ch);

/** Uzbek Latin -> new Uzbek Latin alphabet. */
export function toNewAlphabet(s) {
  return mapChunks(s, (t) => {
    let out = '';
    for (let i = 0; i < t.length; i++) {
      const c = t[i];
      const l = lower(c);
      const n = t[i + 1];
      if ((l === 'o' || l === 'g') && n === OKINA) {
        out += up(l === 'o' ? 'ö' : 'ğ', isUpper(c));
        i++;
      } else if ((l === 's' || l === 'c') && lower(n) === 'h') {
        out += up(l === 's' ? 'ş' : 'ç', isUpper(c));
        i++;
      } else out += c;
    }
    return out;
  });
}

const BASE = {
  a: 'а', b: 'б', d: 'д', f: 'ф', g: 'г', h: 'ҳ', i: 'и', j: 'ж', k: 'к', l: 'л', m: 'м',
  n: 'н', o: 'о', p: 'п', q: 'қ', r: 'р', s: 'с', t: 'т', u: 'у', v: 'в', w: 'в', x: 'х',
  y: 'й', z: 'з', c: 'с', e: 'е',
};
const CYR_VOWELS = 'аэеиоуўяюё';
// "ts" is a native cluster in words like otsiz; it is ц only in these loanword stems.
const TS_AS_C = /^ts(?=iy|ent|ikl|ial|ion|irk|iv|ex)/i;

/** Uzbek Latin -> Uzbek Cyrillic. */
export function toCyrillic(s) {
  return mapChunks(s, (text) =>
    text
      .split(KEEP_LATIN)
      .map((part, k) => (k % 2 ? part : cyr(part)))
      .join(''),
  );
}

// Names and exam acronyms that stay in Latin script inside Cyrillic text.
const KEEP_LATIN =
  /(IELTS|CEFR|TOEFL|SAT|QR|YouTube|Instagram|Google|Authenticator|Aegis|Caps Lock|Start|\/reset)/;

function cyr(t) {
  {
    let out = '';
    const prev = () => out[out.length - 1];
    const wordStart = () => !isLetter(prev());
    for (let i = 0; i < t.length; i++) {
      const c = t[i];
      const l = lower(c);
      const n = lower(t[i + 1]);
      const U = isUpper(c);
      if ((l === 'o' || l === 'g') && t[i + 1] === OKINA) {
        out += up(l === 'o' ? 'ў' : 'ғ', U);
        i++;
      } else if (l === 's' && n === 'h') {
        out += up('ш', U);
        i++;
      } else if (l === 'c' && n === 'h') {
        out += up('ч', U);
        i++;
      } else if (l === 't' && n === 's' && (TS_AS_C.test(t.slice(i)) || (wordStart() && /^ts[aeiou]/i.test(t.slice(i))))) {
        out += up('ц', U);
        i++;
      } else if (l === 'y' && 'auoe'.includes(n || '-') && !(n === 'o' && t[i + 2] === OKINA)) {
        const m = { a: 'я', u: 'ю', o: 'ё', e: 'е' }[n];
        out += up(m, U);
        i++;
      } else if (l === 'e') {
        out += up(wordStart() || CYR_VOWELS.includes(prev()) ? 'э' : 'е', U);
      } else if (c === TUTUQ || c === OKINA) {
        out += 'ъ';
      } else if (BASE[l]) {
        out += up(BASE[l], U);
      } else out += c;
    }
    return out;
  }
}

/** Apply fn to every string leaf of a nested catalog. */
export function mapCatalog(obj, fn) {
  if (typeof obj === 'string') return fn(obj);
  if (Array.isArray(obj)) return obj.map((v) => mapCatalog(v, fn));
  if (obj && typeof obj === 'object') {
    return Object.fromEntries(Object.entries(obj).map(([k, v]) => [k, mapCatalog(v, fn)]));
  }
  return obj;
}
