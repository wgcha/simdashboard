import createClient from 'openapi-fetch'

import type { paths } from './openapi'
import { authenticatedFetch } from '../auth'

export const generatedApiClient = createClient<paths>({ baseUrl: '', fetch: authenticatedFetch })
