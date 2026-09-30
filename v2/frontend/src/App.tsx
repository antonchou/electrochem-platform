import { useState } from 'react';
import { fixed } from './lib/format.ts';
import type { LiveState } from './lib/live.ts';
import { CalibrationPage } from './pages/CalibrationPage.tsx';
import { MeasurePage } from './pages/MeasurePage.tsx';
import { RecordsPage } from './pages/RecordsPage.tsx';
import { useLive } from './useLive.ts';

type Tab = 'measure' | 'records' | 'calibration';

const TABS: { id: Tab; label: string }[] = [
  { id: 'measure', label: '测量' },
  { id: 'records', label: '记录' },
  { id: 'calibration', label: '标定' },
];

const DEVICE_KINDS: Record<string, string> = { sim: '模拟导电池', serial: '串口设备', replay: '回放' };

export function App() {
  const live = useLive();
  const [tab, setTab] = useState<Tab>('measure');
  const [focusId, setFocusId] = useState<number | null>(null);
  const lab = live.lab;

  const showRecord = (id: number) => {
    setFocusId(id);
    setTab('records');
  };

  return (
    <div className="app">
      <header className="topbar">
        <h1>电导率实验平台</h1>
        <nav className="tabs" role="tablist">
          {TABS.map((t) => (
            <button
              key={t.id}
              role="tab"
              aria-selected={tab === t.id}
              className={tab === t.id ? 'tab active' : 'tab'}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </nav>
        <StatusBar live={live} />
      </header>

      {!live.connected && (
        <div className="banner warn" data-testid="banner-disconnected">
          与后端的连接断开了，正在重连……
        </div>
      )}
      {lab?.storage_error && (
        <div className="banner error" data-testid="banner-storage">
          {lab.storage_error}
        </div>
      )}

      <main>
        {tab === 'measure' && <MeasurePage live={live} onShowRecord={showRecord} />}
        {tab === 'records' && <RecordsPage refreshKey={lab?.last_finished?.id ?? null} focusId={focusId} />}
        {tab === 'calibration' && <CalibrationPage lab={lab} />}
      </main>
    </div>
  );
}

function StatusBar({ live }: { live: LiveState }) {
  const lab = live.lab;
  if (!lab) return <div className="status" />;
  const device = lab.device;
  const calibration = lab.calibration;
  return (
    <div className="status">
      <span className={device.connected ? 'pill ok' : 'pill bad'} title={device.message ?? ''} data-testid="device-status">
        <span className="dot" />
        {DEVICE_KINDS[device.kind] ?? device.kind}
        {device.info.device_id ? ` · ${device.info.device_id}` : ''}
        {device.connected ? '' : ' · 未连接'}
      </span>
      <span
        className={calibration ? 'pill ok' : 'pill warn'}
        title={calibration ? `标定 #${calibration.id}` : '未标定：κ 只是标称 Kcell 下的相对值'}
        data-testid="kcell-status"
      >
        Kcell {fixed(lab.cell_constant_per_cm, 4)} cm⁻¹ · {calibration ? `标定 #${calibration.id}` : '未标定'}
      </span>
    </div>
  );
}
