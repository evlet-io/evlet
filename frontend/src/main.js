import { createApp } from 'vue'
import PrimeVue from 'primevue/config'
import Aura from '@primeuix/themes/aura'
import { definePreset } from '@primeuix/themes'
import App from './App.vue'
import './style.css'

const theme = definePreset(Aura, {
  semantic: {
    primary: {
      50: '#f2f7f0', 100: '#dfead7', 200: '#c9dcb7', 300: '#a5c495',
      400: '#729e72', 500: '#467d58', 600: '#376e55', 700: '#28513e',
      800: '#234333', 900: '#18352d', 950: '#10271f',
    },
    colorScheme: { light: { primary: { color: '{primary.600}', hoverColor: '{primary.700}', activeColor: '{primary.800}' } } },
  },
})

createApp(App)
  .use(PrimeVue, { theme: { preset: theme, options: { darkModeSelector: false } } })
  .mount('#app')
