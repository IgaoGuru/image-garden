import {
  createFetchDataSource,
  mountFromDataSource,
  type FetchDataSourceEndpoints,
} from '@image-garden/viewer';

const endpoints: Partial<FetchDataSourceEndpoints> = {
  status: '/viewer/status.json',
  assets: '/viewer/assets.json',
  nearAssets: '/viewer/assets/near',
  asset: (id) => `/viewer/assets/${encodeURIComponent(id)}.json`,
};

const source = createFetchDataSource({
  baseUrl: 'https://example.test',
  endpoints,
  initialLimit: 5000,
});

await mountFromDataSource(document.querySelector<HTMLElement>('#app')!, source, {
  layout: { center: false },
  sprites: { renderMode: 'auto' },
});
