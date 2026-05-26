/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_STATIC_MODE?: string;
  readonly VITE_STATIC_ASSETS_URL?: string;
  readonly VITE_STATIC_STATUS_URL?: string;
  readonly VITE_STATIC_TEXTURE_ARRAY_URL?: string;
  readonly VITE_STATIC_ATLAS_URL?: string;
  readonly VITE_HOSTED_PRODUCTION?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
