import { useQuery } from '@tanstack/react-query'
import { useRef } from 'react'

import { getVersion } from './api'
import HealthPage from './HealthPage'

// the dashboard is one page; a banner says when the server runs old code or this page is older than the server's build
export default function App() {
  const version = useQuery({ queryKey: ['version'], queryFn: getVersion, refetchInterval: 15000, refetchOnWindowFocus: true })
  const loadedBuild = useRef<string | null>(null)
  if (version.data && loadedBuild.current === null) loadedBuild.current = version.data.build
  const outdated = Boolean(version.data && loadedBuild.current !== null && version.data.build !== loadedBuild.current)
  return (
    <main className="main solo">
      {version.data?.restart_needed && (
        <div className="message outdated" role="alert">
          <p>The app was updated while it was running, and the running app still has the old code. Restart it: press Ctrl+C in
            the terminal where it runs, then type <code>run_md_health_dashboard</code>.</p>
        </div>
      )}
      {outdated && (
        <div className="message outdated" role="alert">
          <p>The app was updated. Reload this page to use the new version.</p>
          <button type="button" className="primary" onClick={() => window.location.reload()}>Reload</button>
        </div>
      )}
      <HealthPage />
    </main>
  )
}
