type Props = { selected: string[]; setSelected: (value: string[]) => void }

export function EdgeFilter({ selected, setSelected }: Props) {
  const options = [['top', '상단'], ['bottom', '하단'], ['left', '좌측'], ['right', '우측']]
  const toggle = (key: string) => setSelected(selected.includes(key) ? selected.filter((item) => item !== key) : [...selected, key])
  return <div className="edge-filter"><span>표시 엣지</span>{options.map(([key, label]) => <button className={selected.includes(key) ? 'on' : ''} key={key} onClick={() => toggle(key)}><i />{label}</button>)}</div>
}
