<script setup lang="ts">
import type { Chat, Mode } from '../api/client.ts'
import ChatIcon from './ChatIcon.vue'
defineProps<{ chats: Chat[]; active?: string; busy: boolean; expanded: boolean }>()
defineEmits<{ create: [Mode]; open: [Chat]; reports: []; grants: []; hide: [] }>()
</script>

<template>
  <aside id="chat-navigation" class="sidebar" :class="{ expanded }" aria-label="会话导航">
    <div class="brand">
      <ChatIcon name="mark" :size="36" />
      <strong>GenshinChronicle</strong>
      <button class="nav-close icon-button" aria-label="收起导航" @click="$emit('hide')">
        <ChatIcon name="close" />
      </button>
    </div>
    <div class="new-actions">
      <button class="primary new-chat" :disabled="busy" @click="$emit('create', 'LIVE')">
        <ChatIcon name="plus" />新建对话
      </button>
      <button class="demo-chat" :disabled="busy" @click="$emit('create', 'MOCK')">
        <ChatIcon name="flask" />离线演示
      </button>
    </div>
    <h2 class="small-heading">最近会话</h2>
    <nav class="chat-list">
      <p v-if="!chats.length" class="muted">暂无历史会话</p>
      <button
        v-for="chat in chats"
        :key="chat.id"
        :class="{ active: chat.id === active }"
        :aria-current="chat.id === active ? 'page' : undefined"
        :disabled="busy"
        @click="$emit('open', chat)"
      >
        <ChatIcon name="chat" />
        <span>{{ chat.title }}</span
        ><small v-if="chat.mode === 'MOCK'">演示</small>
      </button>
    </nav>
    <div class="nav-bottom">
      <button @click="$emit('grants')"><ChatIcon name="settings" />批次设置</button>
      <button @click="$emit('reports')"><ChatIcon name="record" />反馈记录</button>
    </div>
  </aside>
</template>
