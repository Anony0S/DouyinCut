<script setup lang="ts">
defineProps<{ name: string }>()
const paths: Record<string, string> = {
  rooms: 'M3 4h7v7H3z M14 4h7v7h-7z M3 15h7v5H3z M14 15h7v5h-7z',
  radio: 'M5 4a11 11 0 0 0 0 16 M19 4a11 11 0 0 1 0 16 M8 7a7 7 0 0 0 0 10 M16 7a7 7 0 0 1 0 10 M12 10a2 2 0 1 0 0 4 2 2 0 0 0 0-4',
  record: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6',
  alert: 'm10.3 4-8 14a2 2 0 0 0 1.7 3h16a2 2 0 0 0 1.7-3l-8-14a2 2 0 0 0-3.4 0 M12 9v4 M12 17h.01',
  check: 'm8 12 3 3 5-6 M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18',
  plus: 'M12 5v14 M5 12h14', close: 'm6 6 12 12 M6 18 18 6',
  search: 'M10.5 3a7.5 7.5 0 1 0 0 15 7.5 7.5 0 0 0 0-15 M16 16l5 5',
  refresh: 'M20 7v5h-5 M4 17v-5h5 M6 6a8 8 0 0 1 13 1l1 5 M4 12l1 5a8 8 0 0 0 13 1',
  clock: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 7v5l3 2',
  video: 'M4 5h12a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1 M17 9l4-2v10l-4-2',
  folder: 'M3 7V5a1 1 0 0 1 1-1h5l2 3h9a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7z',
  external: 'M14 3h7v7 M21 3l-11 11 M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5',
  pause: 'M8 5v14 M16 5v14', play: 'm8 5 11 7-11 7V5z', stop: 'M7 7h10v10H7z',
  settings: 'M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6 M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
  trash: 'M3 6h18 M9 6V3h6v3 M5 6l1 15h12l1-15 M10 10v7 M14 10v7',
  arrow: 'M5 12h14 m-5-5 5 5-5 5',
  layers: 'm12 3 10 5-10 5L2 8l10-5z M2 12l10 5 10-5 M2 16l10 5 10-5',
  bag: 'M5 7h14l1 14H4L5 7z M8 8V6a4 4 0 0 1 8 0v2',
}
</script>
<template><svg class="live-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="paths[name] || paths.rooms" /></svg></template>
