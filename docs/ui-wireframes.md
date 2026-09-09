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

# Host page wireframe

The Host page (主播窗口) adds a compact control bar over the same dark timeline,
bounded to three view-local controls.

```text
┌──────────────── Host viewport ──────────────────────────┐
│                                        [Clear]          │
│                                                        │
│            older messages scroll above                  │
│                                                        │
│ [SC ¥30.00] Carol: Great stream                        │
│ [CAPTAIN] Dana · 1 month                               │
│ Bob sent Star ×2 · ¥1.00                               │
│ Alice: hello                                           │
│                                                        │
│                  [3 new messages]                       │
└──────────── newest item / bottom anchored ─────────────┘
```

- Scrolling away from the bottom pauses follow; new messages keep appending
  (never lost) and are counted.
- The bottom prompt is a native, keyboard-accessible button that shows the
  unread count and returns to the latest messages, resetting the count.
- `[Clear]` removes only the current DOM view and resets the view-local
  follow/unread state; it never touches canonical state, OBS delivery,
  filtering, or the connection, and sends no protocol frame.

