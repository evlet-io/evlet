// Development only: the backend authority a dev-server request stands for, or null to leave it unchanged.
// Only loopback names on a dev-server port are mapped; anything else (other hosts, tenant subdomains) passes through.
export function backendAuthority(value, devPorts, backendPort) {
  if (typeof value !== 'string') return null
  const match = /^(127\.0\.0\.1|localhost):(\d+)$/.exec(value)
  if (!match || !devPorts.includes(match[2])) return null
  return `${match[1]}:${backendPort}`
}
