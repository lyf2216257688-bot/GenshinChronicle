<script setup lang="ts">
const paths = {
  plus: 'M12 5v14M5 12h14',
  chat: 'M20.5 11.3a8 8 0 0 1-8.2 7.8H4l1.5-3.2a7.6 7.6 0 0 1-1.7-4.6 8.4 8.4 0 0 1 16.7 0Z',
  flask: 'M9 3h6m-5 0v6l-4.7 8.1A2.6 2.6 0 0 0 7.6 21h8.8a2.6 2.6 0 0 0 2.3-3.9L14 9V3M8 14h8',
  settings: 'M4 7h3m4 0h9M4 17h9m4 0h3M11 7a2 2 0 1 1-4 0 2 2 0 0 1 4 0Zm6 10a2 2 0 1 1-4 0 2 2 0 0 1 4 0Z',
  record: 'M8 3h8l4 4v13H4V3zm8 0v5h4M8 12h8m-8 4h6',
  flag: 'M5 21V4m0 1c5-4 9 4 15 0v10c-6 4-10-4-15 0',
  edit: 'm14 5 5 5M4 20l1-5L16.5 3.5a2 2 0 0 1 2.8 0l1.2 1.2a2 2 0 0 1 0 2.8L9 19z',
  trash: 'M4 7h16M9 7V4h6v3m3 0-1 13H7L6 7m4 3v7m4-7v7',
  book: 'M12 6c-3-2-6-2-9-1v14c3-1 6-1 9 1 3-2 6-2 9-1V5c-3-1-6-1-9 1Zm0 0v14',
  down: 'm6 9 6 6 6-6',
  right: 'm9 6 6 6-6 6',
  close: 'm6 6 12 12M18 6 6 18',
  send: 'M12 19V5m-6 6 6-6 6 6',
  trace: 'M8 5a2 2 0 1 1-4 0 2 2 0 0 1 4 0Zm12 14a2 2 0 1 1-4 0 2 2 0 0 1 4 0ZM6 7v5a3 3 0 0 0 3 3h6a3 3 0 0 1 3 2M14 5h6m-3-3v6',
  stop: 'M8 6h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2Z',
  resume: 'M9 6l10 6-10 6z',
  menu: 'M4 6h16M4 12h16M4 18h16',
}
withDefaults(defineProps<{ name: keyof typeof paths | 'mark'; size?: number }>(), { size: 16 })
</script>

<template>
  <svg
    :width="size"
    :height="size"
    :viewBox="name === 'mark' ? '0 0 48 48' : '0 0 24 24'"
    fill="none"
    stroke="currentColor"
    stroke-width="1.65"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    <template v-if="name === 'mark'">
      <circle cx="24" cy="24" r="18.5" stroke-width=".8" stroke-opacity=".75" stroke-dasharray="2 3" />
      <path d="M24 4v8m0 24v8M4 24h8m24 0h8M24 9l4.1 11 11 4-11 4-4.1 11-4.1-11L9 24l10.9-4L24 9Z" stroke-width="1.25" />
      <path d="M24 16l2.1 6 5.9 2-5.9 2-2.1 6-2.1-6-5.9-2 5.9-2 2.1-6Z" fill="currentColor" stroke="none" />
    </template>
    <path v-else :d="paths[name]" />
  </svg>
</template>
