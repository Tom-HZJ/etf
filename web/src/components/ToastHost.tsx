import { createContext, useCallback, useContext, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { Toast } from '@astryxdesign/core/Toast';

interface ToastMessage {
  id: number;
  body: ReactNode;
  type: 'info' | 'error';
}

type Notify = (body: ReactNode, type?: 'info' | 'error') => void;

const ToastContext = createContext<Notify>(() => undefined);

export function ToastProvider({ children }: { children: ReactNode }): React.JSX.Element {
  const [message, setMessage] = useState<ToastMessage | null>(null);

  const notify = useCallback<Notify>((body, type = 'info') => {
    setMessage({ id: Date.now() + Math.random(), body, type });
  }, []);

  const value = useMemo(() => notify, [notify]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      {message ? (
        <div className="toast-host" key={message.id}>
          <Toast
            body={message.body}
            type={message.type}
            onDismiss={() => setMessage(null)}
            isAutoHide={message.type === 'info'}
            autoHideDuration={5000}
          />
        </div>
      ) : null}
    </ToastContext.Provider>
  );
}

export function useNotify(): Notify {
  return useContext(ToastContext);
}
