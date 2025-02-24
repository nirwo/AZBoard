import { createApp } from 'vue'
import './style.css'
import App from './App.vue'
import axios from 'axios'
import 'bootstrap'

// Configure axios
axios.defaults.withCredentials = true
axios.defaults.headers.common['X-Requested-With'] = 'XMLHttpRequest'

const app = createApp(App)
app.mount('#app')
