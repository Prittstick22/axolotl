import { XCircleIcon } from '@phosphor-icons/react';
import { cn } from 'cn';
import type { ReactNode } from 'react';

/**
 * One radio row. Template: kit.css `.choice`. The checked row gets the ink outline; an error on it
 * turns the outline red. A disabled row sits on the page ground with a muted title (`.choice.off`).
 */
export function Choice({ name, value, checked, disabled, onPick, title, error, children }: {
  name: string;
  value: string;
  checked: boolean;
  disabled?: boolean;
  onPick: (value: string) => void;
  title: ReactNode;
  error?: string;
  children?: ReactNode;
}) {
  return (
    <div
      className={cn(
        'flex flex-col gap-3 rounded-md border px-4 py-3 has-checked:outline-2 has-checked:-outline-offset-2',
        disabled ? 'border-border bg-background' : 'border-input bg-card',
        error ? 'has-checked:outline-failed' : 'has-checked:outline-foreground',
        'has-focus-visible:ring-2 has-focus-visible:ring-ring has-focus-visible:ring-offset-2 has-focus-visible:ring-offset-background',
      )}
    >
      <label className={cn('flex items-start gap-3', disabled ? 'cursor-not-allowed' : 'cursor-pointer')}>
        <input
          type="radio"
          name={name}
          value={value}
          checked={checked}
          disabled={disabled}
          onChange={() => onPick(value)}
          className="peer sr-only"
        />
        <span
          aria-hidden
          className="mt-0.5 flex size-5 flex-none items-center justify-center rounded-full border border-input bg-card peer-checked:border-2 peer-checked:border-foreground peer-checked:after:size-2.5 peer-checked:after:rounded-full peer-checked:after:bg-foreground peer-checked:after:content-['']"
        />
        <span className="flex min-w-0 flex-col">
          <span className={cn('font-medium', disabled && 'text-muted-foreground')}>{title}</span>
          {error ? (
            <span role="alert" className="flex items-center gap-1 font-medium text-failed">
              <XCircleIcon size={16} aria-hidden className="flex-none" />
              {error}
            </span>
          ) : null}
          {children && <span className="text-muted-foreground">{children}</span>}
        </span>
      </label>
    </div>
  );
}
