import http from './http'

export async function login(username, password) {
  return http.post('/v1/auth/login', { username, password })
}

export async function getMe() {
  return http.get('/v1/auth/me')
}