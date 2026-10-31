/* Secure positive seeds that fit JavaScript's exact integer range. */
(() => {
  const issued = new Set();
  function randomSeed(previous, entropy = globalThis.crypto) {
    if (!entropy?.getRandomValues) throw new Error('浏览器不支持安全随机数，请使用本机 localhost 页面，或取消随机并填写固定种子');
    const words = new Uint32Array(2);
    for (let attempt = 0; attempt < 128; attempt++) {
      entropy.getRandomValues(words);
      const seed = (words[0] & 0x1fffff) * 0x100000000 + words[1];
      if (seed > 0 && seed !== Number(previous) && !issued.has(seed)) {
        issued.add(seed);
        return seed;
      }
    }
    throw new Error('未能取得新的安全随机种子，请重试或填写固定种子');
  }
  const api = {randomSeed};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else globalThis.YuE2Seeds = api;
})();
