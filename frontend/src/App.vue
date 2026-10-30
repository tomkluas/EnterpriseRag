<template>
  <router-view />
</template>

<script setup>
</script>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: #f5f5f5;
  color: #333;
}

:root {
  --primary: #3b82f6;
  --primary-dark: #2563eb;
  --danger: #ef4444;
  --success: #22c55e;
  --border: #e5e7eb;
  --bg: #ffffff;
  --bg-secondary: #f9fafb;
  --text: #111827;
  --text-muted: #6b7280;
  --sidebar-w: 260px;
}
</style>
