;(() => {
  'use strict'

  const DEPTH = 9 // prints actually rendered in the pile
  const $ = (s, el = document) => el.querySelector(s)
  const $$ = (s, el = document) => [...el.querySelectorAll(s)]
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v))
  const mod = (a, n) => ((a % n) + n) % n
  const pad = (n) => String(n).padStart(4, '0')
  const href = (p, v) => p.split('/').map(encodeURIComponent).join('/') + '?v=' + v
  const reduced = matchMedia('(prefers-reduced-motion: reduce)')
  const finePointer = matchMedia('(hover: hover) and (pointer: fine)')
  const ms = (n) => (reduced.matches ? 1 : n)

  const table = $('#table')
  const pile = $('#pile')
  const viewer = $('#viewer')
  const frame = $('.frame', viewer)
  const lo = $('.lo', viewer)
  const hi = $('.hi', viewer)
  const cursor = $('.cursor', viewer)

  let photos = []
  let deck = [] // shuffled indices into photos
  let pos = 0 // absolute position of the top print; deck wraps around
  let busy = false
  let viewing = false
  let box = { bw: 0, bh: 0, b: 10, bb: 24 }
  const cards = new Map() // absolute position -> card
  const salt = Math.random().toString(36).slice(2)

  // --- randomness -----------------------------------------------------------

  function seeded(str) {
    let h = 1779033703 ^ str.length
    for (let i = 0; i < str.length; i++) {
      h = Math.imul(h ^ str.charCodeAt(i), 3432918353)
      h = (h << 13) | (h >>> 19)
    }
    return () => {
      h = Math.imul(h ^ (h >>> 16), 2246822507)
      h = Math.imul(h ^ (h >>> 13), 3266489909)
      return ((h ^= h >>> 16) >>> 0) / 4294967296
    }
  }

  function shuffled(n) {
    const a = Array.from({ length: n }, (_, i) => i)
    for (let i = n - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1))
      ;[a[i], a[j]] = [a[j], a[i]]
    }
    return a
  }

  // --- geometry -------------------------------------------------------------

  const photoAt = (k) => photos[deck[mod(k, deck.length)]]
  const T = (t) => `translate3d(${t.x}px, ${t.y}px, 0) rotate(${t.r}deg) scale(${t.s})`

  function measure() {
    const r = table.getBoundingClientRect()
    const bw = Math.min(r.width * (r.width < 640 ? 0.76 : 0.84), 680)
    const bh = Math.min(r.height * 0.82, 660)
    const b = Math.round(clamp(Math.min(bw, bh) * 0.022, 7, 14))
    box = { bw, bh, b, bb: Math.round(b * 2.5) }
  }

  function sizeOf(p) {
    const { bw, bh, b, bb } = box
    const s = Math.min((bw - 2 * b) / p.w, (bh - b - bb) / p.h)
    return { w: Math.round(p.w * s) + 2 * b, h: Math.round(p.h * s) + b + bb }
  }

  function restOf(p) {
    const rnd = seeded(p.id + salt)
    return {
      x: (rnd() - 0.5) * box.bw * 0.14,
      y: (rnd() - 0.5) * box.bh * 0.08,
      r: (rnd() - 0.5) * 18,
      s: 1,
    }
  }

  function stampOf(p) {
    return p.taken ? p.taken.slice(0, 10).replaceAll('-', '.') : ''
  }

  // --- pile -----------------------------------------------------------------

  function makeCard(k) {
    const p = photoAt(k)
    const { w, h } = sizeOf(p)
    const el = document.createElement('div')
    el.className = 'print'
    el.style.cssText =
      `width:${w}px;height:${h}px;margin:${-h / 2}px 0 0 ${-w / 2}px;` +
      `z-index:${1e7 - k};--b:${box.b}px;--bb:${box.bb}px;--c:${p.color}`
    el.innerHTML =
      '<div class="paper"><div class="emulsion"><img alt="" draggable="false" decoding="async"></div>' +
      `<span class="stamp">${stampOf(p)}</span></div>`
    const img = $('img', el)
    img.onload = () => img.classList.add('in')
    img.src = href(p.thumb, p.v)
    const card = { k, el, p, rest: restOf(p), cur: null }
    el.style.transform = T(card.rest)
    return card
  }

  function sync(enter) {
    const depth = Math.min(DEPTH, deck.length)
    for (const [k, c] of cards) {
      if (k < pos || k >= pos + depth) {
        cards.delete(k)
        c.el.animate([{ opacity: 1 }, { opacity: 0 }], ms(180)).onfinish = () => c.el.remove()
      }
    }
    for (let k = pos + depth - 1; k >= pos; k--) {
      if (cards.has(k)) continue
      const c = makeCard(k)
      cards.set(k, c)
      pile.append(c.el)
      enter?.(c, k - pos, depth)
    }
    for (const [k, c] of cards) c.el.classList.toggle('top', k === pos)
    updateIndex()
  }

  function updateIndex() {
    const n = deck.length
    $$('[data-cur]').forEach((el) => (el.textContent = pad(n ? mod(pos, n) + 1 : 0)))
    $$('[data-total]').forEach((el) => (el.textContent = pad(n)))
  }

  function rebuild() {
    for (const c of cards.values()) c.el.remove()
    cards.clear()
    measure()
    sync()
  }

  const fadeIn = (c) => c.el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: ms(320), easing: 'ease-out' })

  function deal(c, depthIndex, depth) {
    const rnd = Math.random
    const from = {
      x: c.rest.x + (rnd() - 0.5) * 80,
      y: c.rest.y - 30 - rnd() * 50,
      r: c.rest.r + (rnd() - 0.5) * 24,
      s: 1.16,
    }
    c.el.animate(
      [
        { transform: T(from), opacity: 0 },
        { transform: T(c.rest), opacity: 1 },
      ],
      {
        duration: ms(640),
        delay: ms((depth - 1 - depthIndex) * 85),
        easing: 'cubic-bezier(.2,.75,.3,1)',
        fill: 'backwards',
      }
    )
  }

  function flyIn(c, depthIndex) {
    if (depthIndex !== 0) return fadeIn(c)
    const dist = Math.max(innerWidth, innerHeight)
    const from = { x: c.rest.x - dist, y: c.rest.y + (Math.random() - 0.5) * 200, r: c.rest.r - 28, s: 1.03 }
    c.el.classList.add('lifted')
    c.el.animate([{ transform: T(from) }, { transform: T(c.rest) }], {
      duration: ms(560),
      easing: 'cubic-bezier(.2,.8,.25,1)',
    }).onfinish = () => c.el.classList.remove('lifted')
  }

  function toss(c, dx, dy, speed = 0) {
    cards.delete(c.k)
    c.el.classList.remove('top')
    c.el.classList.add('lifted')
    const len = Math.hypot(dx, dy) || 1
    const ux = dx / len
    const uy = dy / len
    const dist = Math.max(innerWidth, innerHeight) * 1.15
    const from = c.cur || c.rest
    const to = {
      x: from.x + ux * dist,
      y: from.y + uy * dist,
      r: from.r + ux * 28 + (Math.random() - 0.5) * 16,
      s: 1.03,
    }
    c.el.animate([{ transform: T(from) }, { transform: T(to) }], {
      duration: ms(clamp(640 - speed * 160, 300, 640)),
      easing: 'cubic-bezier(.25,.55,.35,1)',
      fill: 'forwards',
    }).onfinish = () => c.el.remove()
  }

  function next(dx = 1, dy = (Math.random() - 0.65) * 0.7, speed = 0) {
    const c = cards.get(pos)
    if (!c || busy) return
    toss(c, dx, dy, speed)
    pos++
    sync(fadeIn)
  }

  function prev() {
    if (!deck.length || busy) return
    pos--
    sync(flyIn)
  }

  function shuffle() {
    if (!deck.length || busy) return
    busy = true
    let i = 0
    const leaving = [...cards.values()].sort((a, b) => a.k - b.k)
    cards.clear()
    for (const c of leaving) {
      const a = Math.random() * Math.PI * 2
      setTimeout(() => toss(c, Math.cos(a), Math.sin(a), 1), ms(i++ * 30))
    }
    setTimeout(() => {
      deck = shuffled(photos.length)
      pos = 0
      busy = false
      sync(deal)
    }, ms(380 + leaving.length * 30))
  }

  // --- dragging the top print -------------------------------------------------

  pile.addEventListener('pointerdown', (e) => {
    const c = cards.get(pos)
    if (!c || c.el !== e.target.closest('.print') || busy || e.button > 0) return
    e.preventDefault()
    const el = c.el
    el.setPointerCapture(e.pointerId)
    for (const a of el.getAnimations()) a.finish()

    const rect = el.getBoundingClientRect()
    const lever = clamp((rect.top + rect.height / 2 - e.clientY) / (rect.height / 2), -1, 1)
    const t0 = performance.now()
    const x0 = e.clientX
    const y0 = e.clientY
    let samples = [{ x: x0, y: y0, t: t0 }]
    let moved = false

    const move = (ev) => {
      const dx = ev.clientX - x0
      const dy = ev.clientY - y0
      if (!moved && Math.hypot(dx, dy) > 5) {
        moved = true
        el.classList.add('lifted')
      }
      if (!moved) return
      c.cur = {
        x: c.rest.x + dx,
        y: c.rest.y + dy,
        r: c.rest.r + clamp(dx * 0.05 * lever, -22, 22),
        s: 1.035,
      }
      el.style.transform = T(c.cur)
      const now = performance.now()
      samples.push({ x: ev.clientX, y: ev.clientY, t: now })
      samples = samples.filter((s) => now - s.t < 90)
    }

    const up = (ev) => {
      el.removeEventListener('pointermove', move)
      el.removeEventListener('pointerup', up)
      el.removeEventListener('pointercancel', up)
      if (!moved) {
        if (ev.type === 'pointerup' && performance.now() - t0 < 500) open()
        return
      }
      const dx = ev.clientX - x0
      const dy = ev.clientY - y0
      const first = samples[0]
      const last = samples[samples.length - 1]
      const dt = Math.max(1, last.t - first.t)
      const vx = (last.x - first.x) / dt
      const vy = (last.y - first.y) / dt
      const speed = Math.hypot(vx, vy)
      const far = Math.hypot(dx, dy) > Math.min(170, box.bw * 0.3)

      if (ev.type === 'pointerup' && (far || speed > 0.55)) {
        speed > 0.55 ? toss(c, vx, vy, speed) : toss(c, dx, dy, speed)
        pos++
        sync(fadeIn)
      } else {
        const from = c.cur
        c.cur = null
        el.style.transform = T(c.rest)
        el.animate([{ transform: T(from) }, { transform: T(c.rest) }], {
          duration: ms(460),
          easing: 'cubic-bezier(.3,1.35,.5,1)',
        })
        el.classList.remove('lifted')
      }
    }

    el.addEventListener('pointermove', move)
    el.addEventListener('pointerup', up)
    el.addEventListener('pointercancel', up)
  })

  // --- viewer ---------------------------------------------------------------

  function step(d) {
    pos += d
    sync()
    render()
  }

  function render() {
    const p = photoAt(pos)
    const stage = $('.stage', viewer)
    const cs = getComputedStyle(stage)
    const sw = stage.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight)
    const sh = stage.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom)
    const b = Math.round(clamp(Math.min(sw, sh) * 0.018, 7, 16))
    const s = Math.min((sw - 2 * b) / p.w, (sh - 2 * b) / p.h, 1)
    frame.style.setProperty('--b', b + 'px')
    frame.style.width = Math.round(p.w * s) + 2 * b + 'px'
    frame.style.height = Math.round(p.h * s) + 2 * b + 'px'

    lo.src = href(p.thumb, p.v)
    hi.classList.remove('in')
    hi.dataset.id = p.id
    hi.onload = () => hi.dataset.id === p.id && hi.classList.add('in')
    hi.src = href(p.src, p.v)
    const date = p.taken ? new Date(p.taken) : null
    hi.alt = 'Photograph' + (date ? ', ' + date.toDateString() : '')
    $('[data-date]', viewer).textContent = date
      ? date.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' })
      : ''
    history.replaceState(null, '', '#' + encodeURIComponent(p.id))

    for (const d of [1, -1]) {
      const q = photoAt(pos + d)
      new Image().src = href(q.src, q.v)
    }
  }

  function open(viaKey = false) {
    if (!deck.length) return
    viewing = true
    viewer.hidden = false
    viewer.classList.toggle('can-hover', finePointer.matches)
    render()
    requestAnimationFrame(() => requestAnimationFrame(() => viewer.classList.add('open')))
    if (viaKey) $('[data-act="close"]', viewer).focus({ preventScroll: true })
  }

  function close() {
    viewing = false
    viewer.classList.remove('open')
    cursor.classList.remove('on')
    history.replaceState(null, '', location.pathname + location.search)
    setTimeout(() => !viewing && (viewer.hidden = true), ms(240))
  }

  const stage = $('.stage', viewer)
  let swipe = null

  stage.addEventListener('pointerdown', (e) => {
    swipe = { x: e.clientX, y: e.clientY }
  })

  stage.addEventListener('pointerup', (e) => {
    if (!swipe) return
    const dx = e.clientX - swipe.x
    const dy = e.clientY - swipe.y
    swipe = null
    if (Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy)) step(dx < 0 ? 1 : -1)
    else if (Math.hypot(dx, dy) < 10) step(e.clientX < innerWidth / 2 ? -1 : 1)
  })

  stage.addEventListener('pointermove', (e) => {
    if (!finePointer.matches) return
    cursor.textContent = e.clientX < innerWidth / 2 ? 'prev' : 'next'
    cursor.style.transform = `translate(${e.clientX + 10}px, ${e.clientY + 12}px)`
    cursor.classList.add('on')
  })

  stage.addEventListener('pointerleave', () => cursor.classList.remove('on'))

  // --- controls -------------------------------------------------------------

  const actions = {
    next: () => next(),
    prev,
    shuffle,
    close,
    vnext: () => step(1),
    vprev: () => step(-1),
  }

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-act]')
    if (btn) actions[btn.dataset.act]()
  })

  addEventListener('keydown', (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return
    if ((e.key === 'Enter' || e.key === ' ') && e.target.closest('button, a')) return
    if (viewing) {
      if (e.key === 'Escape') close()
      else if (e.key === 'ArrowRight') step(1)
      else if (e.key === 'ArrowLeft') step(-1)
      return
    }
    const k = e.key
    if (k === 'ArrowRight' || k === 'ArrowDown' || k === ' ') next()
    else if (k === 'ArrowLeft' || k === 'ArrowUp') prev()
    else if (k === 'Enter') open(true)
    else if (k === 's') shuffle()
    else return
    e.preventDefault()
  })

  let resizeTimer
  addEventListener('resize', () => {
    clearTimeout(resizeTimer)
    resizeTimer = setTimeout(() => {
      rebuild()
      if (viewing) render()
    }, 120)
  })

  // --- boot -----------------------------------------------------------------

  if (!finePointer.matches) {
    $('.intro .hint').textContent = 'Swipe the top print away to see the next one. Tap it to look closer.'
  }

  fetch('photos.json', { cache: 'no-cache' })
    .then((r) => (r.ok ? r.json() : []))
    .catch(() => [])
    .then((list) => {
      photos = list
      if (!photos.length) {
        $('.empty').hidden = false
        updateIndex()
        return
      }
      deck = shuffled(photos.length)
      const wanted = decodeURIComponent(location.hash.slice(1))
      const at = wanted ? photos.findIndex((p) => p.id === wanted) : -1
      if (at >= 0) {
        const j = deck.indexOf(at)
        ;[deck[0], deck[j]] = [deck[j], deck[0]]
      }
      measure()
      if (at >= 0) {
        sync()
        open()
      } else {
        sync(deal)
      }
    })
})()
