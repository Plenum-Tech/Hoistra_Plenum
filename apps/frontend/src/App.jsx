import React from 'react';
import { useHoistra } from './logic/useHoistra.js';
import Gate from './screens/Gate.jsx';
import Navigator from './components/shell/Navigator.jsx';
import TopBar from './components/shell/TopBar.jsx';
import OrchestratorDock from './components/shell/OrchestratorDock.jsx';
import Home from './screens/Home.jsx';
import Answer from './screens/Answer.jsx';
import Chat from './screens/Chat.jsx';
import Vendors from './screens/Vendors.jsx';
import Module from './screens/Module.jsx';
import Compliance from './screens/Compliance.jsx';
import CustomReport from './screens/CustomReport.jsx';
import Sessions from './screens/Sessions.jsx';
import Space from './screens/Space.jsx';
import Integrations from './screens/Integrations.jsx';
import ConnectModal from './components/shell/ConnectModal.jsx';
import Buildings from './screens/Buildings.jsx';
import DecisionQueue from './components/shell/DecisionQueue.jsx';
import DetailDrawer from './components/shell/DetailDrawer.jsx';
import CommandPalette from './components/shell/CommandPalette.jsx';
import Toast from './components/shell/Toast.jsx';

export default function App() {
  const vals = useHoistra();
  return (
    <div style={{ minHeight: "100vh", background: "var(--color-bg)", color: "var(--color-text)", fontFamily: "var(--font-body)", display: "flex", flexDirection: "column", paddingLeft: vals.shellPad, transition: "padding-left 0.2s ease" }}>
      {vals.gated ? <Gate vals={vals} /> : null}
      {vals.signedIn ? (
        <>
          {vals.acctOpen ? (
            <>
              <div onClick={vals.closeAcct} style={{ position: "fixed", inset: "0", zIndex: "30" }}></div>
            </>
          ) : null}
          <Navigator vals={vals} />
          <TopBar vals={vals} />
          {vals.orchOpen ? <OrchestratorDock vals={vals} /> : null}
        </>
      ) : null}
      {vals.isHome ? <Home vals={vals} /> : null}
      {vals.isAnswer ? <Answer vals={vals} /> : null}
      {vals.isChat ? <Chat vals={vals} /> : null}
      {vals.isVP ? <Vendors vals={vals} /> : null}
      {vals.isModule ? <Module vals={vals} /> : null}
      {vals.isCC ? <Compliance vals={vals} /> : null}
      {vals.isReport ? <CustomReport vals={vals} /> : null}
      {vals.isSessions ? <Sessions vals={vals} /> : null}
      {vals.isSpace ? <Space vals={vals} /> : null}
      {vals.isInteg ? <Integrations vals={vals} /> : null}
      {vals.intModalOn ? <ConnectModal vals={vals} /> : null}
      {vals.isBuildings ? <Buildings vals={vals} /> : null}
      {vals.queueOpen ? <DecisionQueue vals={vals} /> : null}
      {vals.detailOpen ? <DetailDrawer vals={vals} /> : null}
      {vals.paletteOpen ? <CommandPalette vals={vals} /> : null}
      {vals.toastOn ? <Toast vals={vals} /> : null}
    </div>
  );
}
