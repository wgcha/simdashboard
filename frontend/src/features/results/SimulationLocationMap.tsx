import type { DashboardDistribution } from '../../shared/api/simulationDashboard'

const POSITIONS = [['TOP', '상단'], ['BOT', '하단'], ['LH', '좌측'], ['RH', '우측'], ['TOP_LH', '상단 좌측 코너'], ['TOP_RH', '상단 우측 코너'], ['BOT_LH', '하단 좌측 코너'], ['BOT_RH', '하단 우측 코너']] as const

export function SimulationLocationMap({ data, onSelect }: { data: DashboardDistribution; onSelect: (sceneId: string, memberId: string) => void }) {
  const peaks = data.location_peaks ?? []
  if (!peaks.length) return null
  return <section className="simulation-dashboard__location-map">
    <h3>추출 영역 최대응력 발생 위치</h3>
    <p>범주별 발생 위치 · 동률 모두 표시 · 라인 일부 선택 시 코너 제외 · 단위 미확인</p>
    <div style={{ overflowX: 'auto' }}><table><thead><tr><th>위치</th>{peaks.map((peak) => <th key={`${peak.member_id}:${peak.scene_id}`} title={data.scenes.find((scene) => scene.id === peak.scene_id)?.label}>{data.members.find((member) => member.id === peak.member_id)?.label}<br />{data.scenes.find((scene) => scene.id === peak.scene_id)?.scene_sequence_number ?? '순번 미확인'}</th>)}</tr></thead>
      <tbody>{POSITIONS.map(([position, label]) => <tr key={position}><th>{label}</th>{peaks.map((peak) => <td key={`${peak.member_id}:${peak.scene_id}`}>{peak.locations.some((location) => location.position === position) ? <button type="button" onClick={() => onSelect(peak.scene_id, peak.member_id)} title={peak.scope ?? ''}>● {peak.value}</button> : peak.value == null ? '자료 없음' : '—'}</td>)}</tr>)}</tbody>
    </table></div>
  </section>
}
