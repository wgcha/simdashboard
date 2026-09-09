import { createElement, lazy, type ComponentProps, type ComponentType } from 'react'

/** Share one import between intent prefetch and rendering, including a synchronous warm path. */
export function preloadableScreen<T extends ComponentType<any>>(load: () => Promise<{ default: T }>) {
  let resolved: T | undefined
  let pending: Promise<{ default: T }> | undefined
  const preload = () => pending ??= load().then((module) => {
    resolved = module.default
    return module
  }).catch((reason) => { pending = undefined; throw reason })
  const Deferred = lazy(preload)
  function Screen(props: ComponentProps<T>) { return createElement((resolved ?? Deferred) as ComponentType<ComponentProps<T>>, props) }
  return Object.assign(Screen, { preload })
}
