declare module 'react-grid-layout/build/utils.js' {
  export function compact(
    layout: Array<{ i: string; x: number; y: number; w: number; h: number }>,
    compactType: 'vertical',
    cols: number,
  ): Array<{ i: string; x: number; y: number; w: number; h: number }>
}
