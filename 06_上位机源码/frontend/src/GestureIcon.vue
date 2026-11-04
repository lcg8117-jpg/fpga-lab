<template>
  <svg viewBox="0 0 32 32" fill="none" aria-hidden="true" :class="['gesture-icon', kind]">
    <path v-for="(path,index) in paths" :key="index" :d="path" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
  </svg>
</template>

<script setup>
import {computed} from 'vue'
const props=defineProps({kind:{type:String,default:'hand'}})
const shapes={
  right:['M6 16h20','m20 10 6 6-6 6'],
  left:['M26 16H6','m12 10-6 6 6 6'],
  up:['M16 26V6','m10 12 6-6 6 6'],
  down:['M16 6v20','m10 20 6 6 6-6'],
  horizontal:['M5 16h22','m10 11-5 5 5 5','m22 11 5 5-5 5'],
  near:['M3 16h9','m8 11 5 5-5 5','M29 16h-9','m24 11-5 5 5 5'],
  far:['M13 16H3','m7 11-5 5 5 5','M19 16h10','m25 11 5 5-5 5'],
  turn:['M24.7 10A10 10 0 1 0 26 20','M25 4v7h-7'],
  hand:['M11 17V8a2 2 0 0 1 4 0v7-10a2 2 0 0 1 4 0v10-8a2 2 0 0 1 4 0v9-5a2 2 0 0 1 4 0v8c0 6-3 10-9 10-4 0-6-2-8-5l-4-6a2 2 0 0 1 3-3l4 4'],
}
const paths=computed(()=>shapes[props.kind==='reverse-turn'?'turn':props.kind] || shapes.hand)
</script>
