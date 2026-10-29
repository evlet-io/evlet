import { createContext, useContext } from 'solid-js';
export const OverlayContext = createContext<() => HTMLElement | undefined>();
export function useOverlayMount() {
  return useContext(OverlayContext);
}
