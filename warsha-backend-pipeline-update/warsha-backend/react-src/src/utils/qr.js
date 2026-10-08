/**
 * مُرمِّز QR صغير بدون أي اعتماديات (Model 2، وضع البايت UTF-8، مستوى تصحيح M، إصدارات 1–10).
 * مخصص لعرض رابط مشاركة الفاتورة الإلكترونية (نص قصير) داخل المتصفح بدون CDN.
 *
 * qrMatrix(text)             -> boolean[][]  (true = وحدة داكنة)
 * qrToSvgPath(matrix, quiet) -> { path, size }  لاستخدامه داخل <svg viewBox="0 0 size size">
 *
 * مبني على مواصفة ISO/IEC 18004 (جداول ECC للمستوى M فقط).
 */

const MAX_VERSION = 10
// مستوى M: عدد كلمات التصحيح لكل كتلة، وعدد الكتل — للإصدارات 1..10
const ECC_PER_BLOCK = [0, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26]
const NUM_BLOCKS    = [0, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5]

const bit = (x, i) => ((x >>> i) & 1) !== 0

function rawDataModules(ver) {
  let r = (16 * ver + 128) * ver + 64
  if (ver >= 2) {
    const na = Math.floor(ver / 7) + 2
    r -= (25 * na - 10) * na - 55
    if (ver >= 7) r -= 36
  }
  return r
}

const dataCodewords = (ver) =>
  Math.floor(rawDataModules(ver) / 8) - ECC_PER_BLOCK[ver] * NUM_BLOCKS[ver]

function utf8Bytes(text) {
  return Array.from(new TextEncoder().encode(String(text)))
}

// ── Reed–Solomon على GF(256) / 0x11D ────────────────────────────────
function gfMul(x, y) {
  let z = 0
  for (let i = 7; i >= 0; i--) {
    z = (z << 1) ^ ((z >>> 7) * 0x11d)
    z ^= ((y >>> i) & 1) * x
  }
  return z & 0xff
}

function rsDivisor(degree) {
  const result = new Array(degree).fill(0)
  result[degree - 1] = 1
  let root = 1
  for (let i = 0; i < degree; i++) {
    for (let j = 0; j < degree; j++) {
      result[j] = gfMul(result[j], root)
      if (j + 1 < degree) result[j] ^= result[j + 1]
    }
    root = gfMul(root, 0x02)
  }
  return result
}

function rsRemainder(data, divisor) {
  const result = new Array(divisor.length).fill(0)
  for (const b of data) {
    const factor = b ^ result.shift()
    result.push(0)
    divisor.forEach((coef, i) => { result[i] ^= gfMul(coef, factor) })
  }
  return result
}

function addEccAndInterleave(data, ver) {
  const numBlocks = NUM_BLOCKS[ver]
  const blockEccLen = ECC_PER_BLOCK[ver]
  const rawCodewords = Math.floor(rawDataModules(ver) / 8)
  const numShort = numBlocks - (rawCodewords % numBlocks)
  const shortLen = Math.floor(rawCodewords / numBlocks)
  const blocks = []
  const div = rsDivisor(blockEccLen)
  for (let i = 0, k = 0; i < numBlocks; i++) {
    const dat = data.slice(k, k + shortLen - blockEccLen + (i < numShort ? 0 : 1))
    k += dat.length
    const ecc = rsRemainder(dat, div)
    if (i < numShort) dat.push(0)
    blocks.push(dat.concat(ecc))
  }
  const out = []
  for (let i = 0; i < blocks[0].length; i++) {
    blocks.forEach((blk, j) => {
      if (i !== shortLen - blockEccLen || j >= numShort) out.push(blk[i])
    })
  }
  return out
}

// ── بناء المصفوفة ───────────────────────────────────────────────────
function alignPositions(ver) {
  if (ver === 1) return []
  const n = Math.floor(ver / 7) + 2
  const size = ver * 4 + 17
  const step = Math.ceil((ver * 4 + 4) / (n * 2 - 2)) * 2
  const res = [6]
  for (let pos = size - 7; res.length < n; pos -= step) res.splice(1, 0, pos)
  return res
}

function newGrid(size, v) {
  return Array.from({ length: size }, () => new Array(size).fill(v))
}

function drawFormat(modules, isFn, size, mask) {
  const data = (0 << 3) | mask            // رمز مستوى M = 00
  let rem = data
  for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537)
  const bits = ((data << 10) | rem) ^ 0x5412
  const set = (x, y, d) => { modules[y][x] = d; isFn[y][x] = true }
  for (let i = 0; i <= 5; i++) set(8, i, bit(bits, i))
  set(8, 7, bit(bits, 6))
  set(8, 8, bit(bits, 7))
  set(7, 8, bit(bits, 8))
  for (let i = 9; i < 15; i++) set(14 - i, 8, bit(bits, i))
  for (let i = 0; i < 8; i++) set(size - 1 - i, 8, bit(bits, i))
  for (let i = 8; i < 15; i++) set(8, size - 15 + i, bit(bits, i))
  set(8, size - 8, true)
}

function drawFunctionPatterns(modules, isFn, size, ver) {
  const set = (x, y, d) => { modules[y][x] = d; isFn[y][x] = true }
  for (let i = 0; i < size; i++) { set(6, i, i % 2 === 0); set(i, 6, i % 2 === 0) }
  const finder = (cx, cy) => {
    for (let dy = -4; dy <= 4; dy++) {
      for (let dx = -4; dx <= 4; dx++) {
        const dist = Math.max(Math.abs(dx), Math.abs(dy))
        const xx = cx + dx, yy = cy + dy
        if (xx >= 0 && xx < size && yy >= 0 && yy < size) set(xx, yy, dist !== 2 && dist !== 4)
      }
    }
  }
  finder(3, 3); finder(size - 4, 3); finder(3, size - 4)
  const ap = alignPositions(ver)
  const last = ap.length - 1
  for (let i = 0; i < ap.length; i++) {
    for (let j = 0; j < ap.length; j++) {
      if ((i === 0 && j === 0) || (i === 0 && j === last) || (i === last && j === 0)) continue
      for (let dy = -2; dy <= 2; dy++) {
        for (let dx = -2; dx <= 2; dx++) set(ap[i] + dx, ap[j] + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1)
      }
    }
  }
  drawFormat(modules, isFn, size, 0)   // حجز مكان (يُعاد رسمه بعد اختيار القناع)
  if (ver >= 7) {
    let rem = ver
    for (let i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1f25)
    const bits = (ver << 12) | rem
    for (let i = 0; i < 18; i++) {
      const c = bit(bits, i)
      const a = size - 11 + (i % 3)
      const b = Math.floor(i / 3)
      set(a, b, c); set(b, a, c)
    }
  }
}

function drawCodewords(modules, isFn, size, data) {
  let i = 0
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5
    for (let vert = 0; vert < size; vert++) {
      for (let j = 0; j < 2; j++) {
        const x = right - j
        const upward = ((right + 1) & 2) === 0
        const y = upward ? size - 1 - vert : vert
        if (!isFn[y][x] && i < data.length * 8) {
          modules[y][x] = bit(data[i >>> 3], 7 - (i & 7))
          i++
        }
      }
    }
  }
}

function applyMask(modules, isFn, size, mask) {
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      let inv
      switch (mask) {
        case 0: inv = (x + y) % 2 === 0; break
        case 1: inv = y % 2 === 0; break
        case 2: inv = x % 3 === 0; break
        case 3: inv = (x + y) % 3 === 0; break
        case 4: inv = (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0; break
        case 5: inv = ((x * y) % 2) + ((x * y) % 3) === 0; break
        case 6: inv = (((x * y) % 2) + ((x * y) % 3)) % 2 === 0; break
        default: inv = (((x + y) % 2) + ((x * y) % 3)) % 2 === 0
      }
      if (!isFn[y][x] && inv) modules[y][x] = !modules[y][x]
    }
  }
}

// عقوبات اختيار القناع (القواعد الأربع في المواصفة) — تؤثر على سهولة المسح فقط لا على صحة الفك
function penalty(modules, size) {
  let score = 0
  const lineScore = (get) => {
    let s = 0, run = 1
    for (let i = 1; i < size; i++) {
      if (get(i) === get(i - 1)) { run++ } else { if (run >= 5) s += run - 2; run = 1 }
    }
    if (run >= 5) s += run - 2
    const pat1 = [true, false, true, true, true, false, true, false, false, false, false]
    const pat2 = [false, false, false, false, true, false, true, true, true, false, true]
    for (let i = 0; i + 11 <= size; i++) {
      let m1 = true, m2 = true
      for (let k = 0; k < 11; k++) {
        const v = get(i + k)
        if (v !== pat1[k]) m1 = false
        if (v !== pat2[k]) m2 = false
      }
      if (m1) s += 40
      if (m2) s += 40
    }
    return s
  }
  for (let y = 0; y < size; y++) score += lineScore((i) => modules[y][i])
  for (let x = 0; x < size; x++) score += lineScore((i) => modules[i][x])
  for (let y = 0; y < size - 1; y++) {
    for (let x = 0; x < size - 1; x++) {
      const c = modules[y][x]
      if (c === modules[y][x + 1] && c === modules[y + 1][x] && c === modules[y + 1][x + 1]) score += 3
    }
  }
  let dark = 0
  for (const row of modules) for (const c of row) if (c) dark++
  const total = size * size
  score += Math.floor(Math.abs(dark * 20 - total * 10) / total) * 10
  return score
}

export function qrMatrix(text) {
  const bytes = utf8Bytes(text)
  let ver = 0
  for (let v = 1; v <= MAX_VERSION; v++) {
    const countBits = v <= 9 ? 8 : 16
    if (4 + countBits + bytes.length * 8 <= dataCodewords(v) * 8) { ver = v; break }
  }
  if (!ver) throw new Error('النص أطول من أن يُحوَّل إلى رمز QR')

  // تدفّق البتات: مؤشر الوضع 0100 + العدد + البيانات + الإنهاء + الحشو
  const bits = []
  const push = (val, len) => { for (let i = len - 1; i >= 0; i--) bits.push((val >>> i) & 1) }
  push(0x4, 4)
  push(bytes.length, ver <= 9 ? 8 : 16)
  bytes.forEach(b => push(b, 8))
  const capBits = dataCodewords(ver) * 8
  push(0, Math.min(4, capBits - bits.length))
  while (bits.length % 8) bits.push(0)
  for (let pad = 0xec; bits.length < capBits; pad ^= 0xec ^ 0x11) push(pad, 8)
  const data = new Array(bits.length / 8).fill(0)
  bits.forEach((b, i) => { data[i >>> 3] |= b << (7 - (i & 7)) })

  const all = addEccAndInterleave(data, ver)
  const size = ver * 4 + 17
  const modules = newGrid(size, false)
  const isFn = newGrid(size, false)
  drawFunctionPatterns(modules, isFn, size, ver)
  drawCodewords(modules, isFn, size, all)

  let bestMask = 0, bestScore = Infinity
  for (let m = 0; m < 8; m++) {
    applyMask(modules, isFn, size, m)
    drawFormat(modules, isFn, size, m)
    const s = penalty(modules, size)
    if (s < bestScore) { bestScore = s; bestMask = m }
    applyMask(modules, isFn, size, m)   // XOR ثانية = إلغاء
  }
  applyMask(modules, isFn, size, bestMask)
  drawFormat(modules, isFn, size, bestMask)
  return modules
}

/** مسار SVG واحد لكل الوحدات الداكنة (مع هامش هادئ quiet modules). */
export function qrToSvgPath(matrix, quiet = 4) {
  const n = matrix.length
  let d = ''
  for (let y = 0; y < n; y++) {
    for (let x = 0; x < n; x++) {
      if (matrix[y][x]) d += `M${x + quiet},${y + quiet}h1v1h-1z`
    }
  }
  return { path: d, size: n + quiet * 2 }
}
