/**
 * Minimal Web Crypto polyfill for non-secure contexts (plain HTTP on a LAN IP).
 *
 * Browsers only expose `crypto.subtle` and `crypto.randomUUID` in a secure
 * context (HTTPS or localhost). keycloak-js needs BOTH during init:
 *   - `crypto.randomUUID()`            — for state/session ids
 *   - `crypto.subtle.digest('SHA-256')` — for the PKCE S256 challenge
 * `crypto.getRandomValues` is NOT gated and works over HTTP, so the PKCE
 * verifier keeps full entropy and we can build the rest on top of it.
 *
 * This fills in the two missing pieces *only when the native ones are absent*.
 * It is a no-op in secure contexts.
 */

// FIPS 180-4 round constants.
const K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
  0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
  0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
  0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
  0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
  0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
  0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
  0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
  0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

const rotr = (x: number, n: number) => ((x >>> n) | (x << (32 - n))) >>> 0;

function sha256(bytes: Uint8Array): Uint8Array {
  const H = new Uint32Array([
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c,
    0x1f83d9ab, 0x5be0cd19,
  ]);

  const bitLen = bytes.length * 8;
  const withOne = bytes.length + 1;
  const padZeros = (56 - (withOne % 64) + 64) % 64;
  const total = withOne + padZeros + 8;

  const m = new Uint8Array(total);
  m.set(bytes);
  m[bytes.length] = 0x80;
  const dv = new DataView(m.buffer);
  dv.setUint32(total - 8, Math.floor(bitLen / 0x100000000));
  dv.setUint32(total - 4, bitLen >>> 0);

  const w = new Uint32Array(64);
  for (let off = 0; off < total; off += 64) {
    for (let i = 0; i < 16; i++) w[i] = dv.getUint32(off + i * 4);
    for (let i = 16; i < 64; i++) {
      const s0 =
        rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
      const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
    }

    let [a, b, c, d, e, f, g, h] = H;
    for (let i = 0; i < 64; i++) {
      const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const t1 = (h + S1 + ch + K[i] + w[i]) >>> 0;
      const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (S0 + maj) >>> 0;
      h = g;
      g = f;
      f = e;
      e = (d + t1) >>> 0;
      d = c;
      c = b;
      b = a;
      a = (t1 + t2) >>> 0;
    }

    H[0] = (H[0] + a) >>> 0;
    H[1] = (H[1] + b) >>> 0;
    H[2] = (H[2] + c) >>> 0;
    H[3] = (H[3] + d) >>> 0;
    H[4] = (H[4] + e) >>> 0;
    H[5] = (H[5] + f) >>> 0;
    H[6] = (H[6] + g) >>> 0;
    H[7] = (H[7] + h) >>> 0;
  }

  const out = new Uint8Array(32);
  const outView = new DataView(out.buffer);
  for (let i = 0; i < 8; i++) outView.setUint32(i * 4, H[i]);
  return out;
}

function toBytes(data: BufferSource): Uint8Array {
  if (data instanceof ArrayBuffer) return new Uint8Array(data);
  const view = data as ArrayBufferView;
  return new Uint8Array(view.buffer, view.byteOffset, view.byteLength);
}

/** RFC 4122 v4 UUID built from the (HTTP-available) getRandomValues RNG. */
function uuidv4(c: Crypto): string {
  const b = c.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40; // version 4
  b[8] = (b[8] & 0x3f) | 0x80; // variant 10xx
  const hex = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(
    16,
    20,
  )}-${hex.slice(20)}`;
}

function makeSubtle() {
  return {
    digest(algorithm: AlgorithmIdentifier, data: BufferSource): Promise<ArrayBuffer> {
      const name = typeof algorithm === 'string' ? algorithm : algorithm.name;
      if (name.toUpperCase() !== 'SHA-256') {
        return Promise.reject(
          new Error(`webcrypto-polyfill: unsupported digest algorithm "${name}"`),
        );
      }
      return Promise.resolve(sha256(toBytes(data)).buffer as ArrayBuffer);
    },
  };
}

/** Define `name` on the Crypto instance, falling back to its prototype. */
function defineCryptoProp(c: Crypto, name: string, value: unknown): void {
  try {
    Object.defineProperty(c, name, { value, configurable: true });
    if (typeof (c as unknown as Record<string, unknown>)[name] !== 'undefined') return;
  } catch {
    /* fall through */
  }
  try {
    const proto = Object.getPrototypeOf(c);
    if (proto) {
      Object.defineProperty(proto, name, { configurable: true, get: () => value });
    }
  } catch {
    /* fall through */
  }
}

/** Fill in crypto.randomUUID and crypto.subtle.digest when missing (HTTP). */
export function installWebCryptoPolyfill(): void {
  if (typeof window === 'undefined') return;
  const c = window.crypto;
  if (!c || typeof c.getRandomValues !== 'function') return;

  const needUUID = typeof c.randomUUID !== 'function';
  const needSubtle = !c.subtle || typeof c.subtle.digest !== 'function';
  if (!needUUID && !needSubtle) return; // native Web Crypto present

  if (needUUID) defineCryptoProp(c, 'randomUUID', () => uuidv4(c));
  if (needSubtle) defineCryptoProp(c, 'subtle', makeSubtle());

  const okUUID = typeof window.crypto.randomUUID === 'function';
  const okSubtle = typeof window.crypto.subtle?.digest === 'function';

  // Last resort: replace window.crypto wholesale, keeping the native RNG.
  if (!okUUID || !okSubtle) {
    try {
      Object.defineProperty(window, 'crypto', {
        configurable: true,
        value: {
          getRandomValues: c.getRandomValues.bind(c),
          randomUUID: okUUID ? c.randomUUID.bind(c) : () => uuidv4(c),
          subtle: okSubtle ? c.subtle : makeSubtle(),
        },
      });
    } catch {
      /* give up */
    }
  }

  if (
    typeof window.crypto.randomUUID === 'function' &&
    typeof window.crypto.subtle?.digest === 'function'
  ) {
    console.info(
      '[webcrypto-polyfill] crypto shim active: randomUUID + SHA-256 (insecure context)',
    );
  } else {
    console.warn('[webcrypto-polyfill] could not install crypto shim');
  }
}

// Install eagerly as soon as this module loads on the client, so the shim is in
// place before keycloak-js ever reads crypto.randomUUID / crypto.subtle.
installWebCryptoPolyfill();
