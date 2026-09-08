import { useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'

import { useAuth } from '@/auth/useAuth'
import { UserMenu } from '@/components/layout/UserMenu'
import { cn } from '@/lib/cn'
import {
  BookIcon,
  BriefcaseIcon,
  CloseIcon,
  DashboardIcon,
  LogoutIcon,
  MenuIcon,
  QueueIcon,
  RobotIcon,
  UsersIcon,
} from '@/components/ui/Icons'

interface NavItem {
  to: string
  label: string
  icon: typeof DashboardIcon
  comingSoon?: boolean
}

interface NavGroup {
  label: string
  items: NavItem[]
}

// Same six destinations, same routes, same labels as before -- grouped into
// sections instead of one flat list, so the sidebar reads as a map of the
// product (an overview, the screening pipeline, configuration) instead of a
// pile of equally-weighted links.
const NAV_GROUPS: NavGroup[] = [
  {
    label: 'Overview',
    items: [{ to: '/', label: 'Dashboard', icon: DashboardIcon }],
  },
  {
    label: 'Pipeline',
    items: [
      { to: '/positions', label: 'Positions', icon: BriefcaseIcon },
      { to: '/candidates', label: 'Candidates', icon: UsersIcon },
      { to: '/queues', label: 'Queues', icon: QueueIcon },
    ],
  },
  {
    label: 'Configuration',
    items: [
      { to: '/agent-config', label: 'Agent Configuration', icon: RobotIcon },
      { to: '/knowledge', label: 'Knowledge', icon: BookIcon, comingSoon: true },
    ],
  },
]

// Kept for the topbar's slim wayfinding label -- same lookup that used to
// drive a full second heading duplicating PageHeader's own title.
function pageTitle(pathname: string): string {
  if (pathname === '/') return 'Dashboard'
  if (pathname.startsWith('/positions')) return 'Positions'
  if (pathname.startsWith('/candidates')) return 'Candidates'
  if (pathname.startsWith('/agent-config')) return 'Agent Configuration'
  if (pathname.startsWith('/queues')) return 'Queues'
  if (pathname.startsWith('/knowledge')) return 'Knowledge'
  return 'Talentlane'
}

function Logo() {
  return (
    <div className="flex items-center gap-2.5">
      <div className="bg-brand-600 flex h-8 w-8 items-center justify-center rounded-lg font-bold text-white">
        T
      </div>
      <div className="leading-tight">
        <div className="text-ink-900 text-sm font-semibold">Talentlane</div>
        <div className="text-ink-500 text-[11px]">AI Screening Workspace</div>
      </div>
    </div>
  )
}

function SidebarNav({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav className="flex-1 space-y-6 overflow-y-auto px-3 py-5" aria-label="Main">
      {NAV_GROUPS.map((group) => (
        <div key={group.label}>
          <h2 className="text-ink-400 px-3 text-[11px] font-semibold tracking-wide uppercase">
            {group.label}
          </h2>
          <div className="mt-1.5 space-y-0.5">
            {group.items.map(({ to, label, icon: ItemIcon, comingSoon }) => (
              <NavLink
                key={to}
                to={to}
                end={to === '/'}
                onClick={onNavigate}
                className={({ isActive }) =>
                  cn(
                    'group relative flex items-center gap-3 rounded-lg py-2 pr-3 pl-3.5 text-sm font-medium',
                    'transition-standard transition-colors',
                    isActive
                      ? 'bg-brand-50 text-brand-700'
                      : 'text-ink-600 hover:bg-ink-100 hover:text-ink-900',
                  )
                }
              >
                {({ isActive }) => (
                  <>
                    {/* Accent rail on the active item -- a second, quieter
                        signal alongside the tint so active state reads even
                        for a color-blind viewer, not just via the fill. */}
                    <span
                      aria-hidden="true"
                      className={cn(
                        'absolute top-1/2 left-0 h-4 w-[3px] -translate-y-1/2 rounded-full',
                        'transition-standard transition-colors',
                        isActive ? 'bg-brand-600' : 'bg-transparent',
                      )}
                    />
                    <ItemIcon className="h-[18px] w-[18px] shrink-0" />
                    <span className="truncate">{label}</span>
                    {comingSoon && (
                      <span className="bg-ink-100 text-ink-500 ml-auto rounded px-1.5 py-0.5 text-[10px] font-semibold tracking-wide uppercase">
                        Soon
                      </span>
                    )}
                  </>
                )}
              </NavLink>
            ))}
          </div>
        </div>
      ))}
    </nav>
  )
}

export function AppLayout() {
  const { email, logout } = useAuth()
  const location = useLocation()
  const [mobileOpen, setMobileOpen] = useState(false)

  return (
    <div className="bg-surface-page min-h-screen lg:flex">
      {/* Visually hidden until focused -- the first Tab stop on any page,
          letting a keyboard user jump straight past the sidebar. */}
      <a
        href="#main-content"
        className="bg-brand-600 transition-standard fixed top-3 left-3 z-50 -translate-y-16 rounded-lg px-4 py-2 text-sm font-medium text-white transition-transform focus-visible:translate-y-0"
      >
        Skip to content
      </a>

      {/* Desktop sidebar */}
      <aside className="border-border-default bg-surface-raised hidden w-64 shrink-0 flex-col border-r lg:flex">
        <div className="border-border-default flex h-16 items-center border-b px-5">
          <Logo />
        </div>
        <SidebarNav />
        <div className="border-border-default border-t p-3">
          <button
            type="button"
            onClick={logout}
            className="text-ink-600 hover:bg-ink-100 hover:text-ink-900 transition-standard flex w-full items-center gap-3 rounded-lg px-3.5 py-2 text-sm font-medium transition-colors"
          >
            <LogoutIcon className="h-[18px] w-[18px]" />
            Sign out
          </button>
        </div>
      </aside>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button
            type="button"
            aria-label="Close navigation"
            className="bg-ink-950/40 absolute inset-0"
            onClick={() => setMobileOpen(false)}
          />
          <aside className="bg-surface-raised absolute inset-y-0 left-0 flex w-72 flex-col shadow-xl">
            <div className="border-border-default flex h-16 items-center justify-between border-b px-5">
              <Logo />
              <button
                type="button"
                aria-label="Close navigation"
                onClick={() => setMobileOpen(false)}
                className="text-ink-500 hover:text-ink-900"
              >
                <CloseIcon />
              </button>
            </div>
            <SidebarNav onNavigate={() => setMobileOpen(false)} />
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="border-border-default bg-surface-raised/90 sticky top-0 z-30 flex h-16 items-center gap-4 border-b px-4 backdrop-blur sm:px-6 lg:px-8">
          <button
            type="button"
            aria-label="Open navigation"
            onClick={() => setMobileOpen(true)}
            className="text-ink-600 hover:bg-ink-100 -ml-1 rounded-lg p-2 lg:hidden"
          >
            <MenuIcon />
          </button>

          {/* Slim wayfinding, not a second heading -- PageHeader already
              carries the real, prominent title in the content area below;
              this exists only so a page is identifiable on mobile, where the
              sidebar (and its active-item highlight) is hidden. */}
          <p className="text-ink-400 hidden truncate text-xs font-medium tracking-wide uppercase sm:block">
            {pageTitle(location.pathname)}
          </p>

          <div className="ml-auto flex items-center gap-1">
            <UserMenu email={email} onLogout={logout} />
          </div>
        </header>

        <main
          id="main-content"
          tabIndex={-1}
          className="mx-auto w-full max-w-7xl flex-1 px-4 py-8 outline-none sm:px-6 lg:px-8 lg:py-10"
        >
          <Outlet />
        </main>
      </div>
    </div>
  )
}
