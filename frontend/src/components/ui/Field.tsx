import { useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from 'react'

import { cn } from '@/lib/cn'

const CONTROL_CLASSES =
  'w-full rounded-lg border border-border-strong bg-surface-raised px-3 py-2 text-sm text-ink-900 ' +
  'transition-standard transition-[border-color] placeholder:text-ink-400 hover:border-ink-400 ' +
  'disabled:cursor-not-allowed disabled:bg-ink-100'

const INVALID_CLASSES = 'border-danger-400 hover:border-danger-500 focus-visible:outline-danger-600'

interface LabelledProps {
  label: string
  hint?: ReactNode
  /** Validation message. When present, replaces the hint and marks the
   *  control aria-invalid, describedby-linked to this text. */
  error?: string
  required?: boolean
}

function FieldShell({
  label,
  hint,
  error,
  required,
  htmlFor,
  descriptionId,
  children,
}: LabelledProps & { htmlFor: string; descriptionId: string; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="text-ink-700 block text-sm font-medium">
        {label}
        {required && (
          <span aria-hidden="true" className="text-danger-600 ml-0.5">
            *
          </span>
        )}
      </label>
      {children}
      {error ? (
        <p id={descriptionId} role="alert" className="text-danger-600 text-xs font-medium">
          {error}
        </p>
      ) : (
        hint && (
          <p id={descriptionId} className="text-ink-500 text-xs">
            {hint}
          </p>
        )
      )}
    </div>
  )
}

type TextFieldProps = LabelledProps & InputHTMLAttributes<HTMLInputElement>

export function TextField({ label, hint, error, required, className, ...props }: TextFieldProps) {
  const generatedId = useId()
  const descriptionId = useId()
  const id = props.id ?? generatedId
  const hasDescription = Boolean(error || hint)
  return (
    <FieldShell
      label={label}
      hint={hint}
      error={error}
      required={required}
      htmlFor={id}
      descriptionId={descriptionId}
    >
      <input
        id={id}
        required={required}
        aria-invalid={error ? true : undefined}
        aria-describedby={hasDescription ? descriptionId : undefined}
        className={cn(CONTROL_CLASSES, error && INVALID_CLASSES, className)}
        {...props}
      />
    </FieldShell>
  )
}

type TextAreaFieldProps = LabelledProps & TextareaHTMLAttributes<HTMLTextAreaElement>

export function TextAreaField({
  label,
  hint,
  error,
  required,
  className,
  ...props
}: TextAreaFieldProps) {
  const generatedId = useId()
  const descriptionId = useId()
  const id = props.id ?? generatedId
  const hasDescription = Boolean(error || hint)
  return (
    <FieldShell
      label={label}
      hint={hint}
      error={error}
      required={required}
      htmlFor={id}
      descriptionId={descriptionId}
    >
      <textarea
        id={id}
        required={required}
        aria-invalid={error ? true : undefined}
        aria-describedby={hasDescription ? descriptionId : undefined}
        className={cn(CONTROL_CLASSES, 'min-h-24 resize-y', error && INVALID_CLASSES, className)}
        {...props}
      />
    </FieldShell>
  )
}

type SelectFieldProps = LabelledProps & SelectHTMLAttributes<HTMLSelectElement>

export function SelectField({
  label,
  hint,
  error,
  required,
  className,
  children,
  ...props
}: SelectFieldProps) {
  const generatedId = useId()
  const descriptionId = useId()
  const id = props.id ?? generatedId
  const hasDescription = Boolean(error || hint)
  return (
    <FieldShell
      label={label}
      hint={hint}
      error={error}
      required={required}
      htmlFor={id}
      descriptionId={descriptionId}
    >
      <select
        id={id}
        required={required}
        aria-invalid={error ? true : undefined}
        aria-describedby={hasDescription ? descriptionId : undefined}
        className={cn(CONTROL_CLASSES, error && INVALID_CLASSES, className)}
        {...props}
      >
        {children}
      </select>
    </FieldShell>
  )
}
