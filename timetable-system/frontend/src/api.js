import axios from 'axios'

const api = axios.create({ headers: { 'X-Timetable-Request': '1' } })
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401 && !error.config?.url?.startsWith('/api/auth/')) {
      // Reload also clears timetable data, open dialogs and pending UI state.
      window.location.reload()
    }
    return Promise.reject(error)
  },
)
export default api
