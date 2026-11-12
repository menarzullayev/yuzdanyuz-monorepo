import { describe, expect, it } from 'vitest';
import { mapCatalog, normalizeApostrophes, toCyrillic, toNewAlphabet } from '../scripts/translit.mjs';

const O = 'ʻ'; // okina
const T = 'ʼ'; // tutuq belgisi

describe('new Uzbek alphabet', () => {
  it.each([
    [`o${O}zbek`, 'özbek'],
    [`g${O}isht`, 'ğişt'],
    ['shahar', 'şahar'],
    ['choy', 'çoy'],
    [`O${O}zbekiston G${O}alaba`, 'Özbekiston Ğalaba'],
    ['Shaxmat Chorak', 'Şaxmat Çorak'],
    ['SHARQ CHEGARA', 'ŞARQ ÇEGARA'],
    [`O${O}QUVCHI`, 'ÖQUVÇI'],
  ])('%s -> %s', (src, want) => {
    expect(toNewAlphabet(src)).toBe(want);
  });

  it('keeps the tutuq apostrophe and does not merge s + h across it', () => {
    expect(toNewAlphabet(`ma${T}no`)).toBe(`ma${T}no`);
    expect(toNewAlphabet(`as${T}hob`)).toBe(`as${T}hob`);
    expect(toNewAlphabet(`mas${T}ul`)).toBe(`mas${T}ul`);
  });

  it('repairs legacy ASCII apostrophes', () => {
    expect(normalizeApostrophes("bo'lim ma'no")).toBe(`bo${O}lim ma${T}no`);
    expect(toNewAlphabet("bo'lim")).toBe('bölim');
  });

  it('never touches ICU arguments or tags', () => {
    expect(toNewAlphabet('{shaxs} <b>shahar</b> {count, plural, one {choy}}')).toBe(
      '{shaxs} <b>şahar</b> {count, plural, one {choy}}',
    );
  });
});

describe('Uzbek Cyrillic', () => {
  it.each([
    ['salom', 'салом'],
    [`o${O}zbek`, 'ўзбек'],
    [`g${O}isht`, 'ғишт'],
    ['shahar', 'шаҳар'],
    ['choy', 'чой'],
    ['qalam', 'қалам'],
    ['xat', 'хат'],
    ['hayot', 'ҳаёт'],
    ['jurnal', 'журнал'],
    ['tong', 'тонг'],
  ])('%s -> %s', (src, want) => {
    expect(toCyrillic(src)).toBe(want);
  });

  it('ya / yu / yo / ye -> я ю ё е', () => {
    expect(toCyrillic('yaxshi')).toBe('яхши');
    expect(toCyrillic('yulduz')).toBe('юлдуз');
    expect(toCyrillic('yoz')).toBe('ёз');
    expect(toCyrillic('yetti')).toBe('етти');
    expect(toCyrillic('Yangi YOZ')).toBe('Янги ЁЗ');
  });

  it('yo before o-okina is й + ў, not ё', () => {
    expect(toCyrillic(`yo${O}l`)).toBe('йўл');
  });

  it('e is э at the start of a word or after a vowel, е elsewhere', () => {
    expect(toCyrillic('eshik')).toBe('эшик');
    expect(toCyrillic('Elektron')).toBe('Электрон');
    expect(toCyrillic('maktab elchi')).toBe('мактаб элчи');
    expect(toCyrillic('kelgan')).toBe('келган');
    expect(toCyrillic('poezd')).toBe('поэзд');
  });

  it('ц only in loanword stems, native "ts" stays т + с', () => {
    expect(toCyrillic('konstitutsiya')).toBe('конституция');
    expect(toCyrillic('tsentr')).toBe('центр');
    expect(toCyrillic('otsiz')).toBe('отсиз');
  });

  it('tutuq becomes ъ and s+h across it is not ш', () => {
    expect(toCyrillic(`ma${T}no`)).toBe('маъно');
    expect(toCyrillic(`as${T}hob`)).toBe('асъҳоб');
    expect(toCyrillic("ta'sir")).toBe('таъсир');
  });

  it('keeps ICU arguments, tags and listed Latin names', () => {
    expect(toCyrillic('{shaxs} salom <b>choy</b>')).toBe('{shaxs} салом <b>чой</b>');
    expect(toCyrillic('IELTS va Google bilan')).toBe('IELTS ва Google билан');
  });
});

describe('mapCatalog', () => {
  it('maps nested string leaves only', () => {
    expect(mapCatalog({ a: { b: 'shahar', n: 3 }, c: ['choy'] }, toNewAlphabet)).toEqual({
      a: { b: 'şahar', n: 3 },
      c: ['çoy'],
    });
  });
});
