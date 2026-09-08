/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the FastAPI backend; blank in dev, where Vite proxies /api. */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
