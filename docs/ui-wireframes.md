# OBS page wireframe — First Vertical Slice

Only the transparent OBS Browser Source is designed here.

```text
┌──────────────── Browser Source viewport ────────────────┐
│ transparent; no title, controls, status, or scrollbar   │
│                                                        │
│                       older items clip above            │
│                                                        │
│ [SC ¥30.00] Carol: Great stream                        │
│ [CAPTAIN] Dana · 1 month                               │
│ Bob sent Star ×2 · ¥1.00                               │
│ Alice: hello                                           │
└──────── newest item / bottom anchored ─────────────────┘
```

At startup, empty snapshot, disconnect, or reconnect, the viewport remains
fully transparent with no user-visible error. A snapshot appears without entry
animation. New increments append at the bottom; overflow clips at the top and
the DOM retains at most 100 items. Narrow width wraps text without scaling font
or spacing. User-controlled text is plain text, never markup.

Variant cues are fixed for this slice: danmaku is high-contrast text; gift,
guard, and super chat are distinct preset cards. No settings, filters, desktop
window, super-chat pin strip, or interactive controls are included.

