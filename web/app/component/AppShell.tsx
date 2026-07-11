'use client';

import { useState } from 'react';
import Sidebar from './Sidebar';
import Topbar from './Topbar';
import { SettingsModal, NotificationsDrawer, ProfileDrawer } from './Modals';

type ModalType = 'settings' | 'notifications' | 'profile' | null;

export default function AppShell({ children }: { children: React.ReactNode }) {
  const [modal, setModal] = useState<ModalType>(null);

  return (
    <div className="ix">
      <Sidebar onOpenModal={setModal} />
      <div className="ix-main">
        <Topbar />
        <main className="ix-page">{children}</main>
      </div>

      {modal === 'settings'      && <SettingsModal        onClose={() => setModal(null)} />}
      {modal === 'notifications' && <NotificationsDrawer  onClose={() => setModal(null)} />}
      {modal === 'profile'       && <ProfileDrawer        onClose={() => setModal(null)} />}
    </div>
  );
}
