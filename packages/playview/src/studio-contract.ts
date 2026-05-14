export const REQUIRED_STUDIO_API_VERSION = '0.1';
export const COMPATIBLE_VIEWER_RANGE = '^0.1.0';
export const COMPATIBLE_STUDIO_PACKAGE_RANGE = '0.1.x';

export interface StudioStatusContract {
  studioApiVersion?: string;
  studioVersion?: string;
}

export function assertCompatibleStudioStatus(status: StudioStatusContract): void {
  if (status.studioApiVersion !== REQUIRED_STUDIO_API_VERSION) {
    const found = status.studioApiVersion ?? 'missing';
    throw new Error(
      `Playview requires Image Garden Studio API ${REQUIRED_STUDIO_API_VERSION}; got ${found}. `
      + `Use image-garden-studio ${COMPATIBLE_STUDIO_PACKAGE_RANGE}.`,
    );
  }
}
