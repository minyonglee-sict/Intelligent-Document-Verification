import type { InvoiceFields, LineItem } from '../types'

/**
 * 추출 결과 편집 폼. Streamlit 검수 화면의 field_editor 에 대응한다.
 *
 * 값은 부모가 들고, 여기서는 바뀐 것만 올려 보낸다. 빈 칸은 빈 문자열이 아니라
 * null 로 보낸다 -- 엔진은 '없음'과 '빈 문자열'을 다르게 다룬다.
 */

const DOC_TYPES = [
  { value: 'INVOICE', label: '송장' },
  { value: 'RECEIPT', label: '영수증' },
  { value: 'UNKNOWN', label: '미분류' },
] as const

function toNumber(raw: string): number | null {
  const trimmed = raw.trim()
  if (!trimmed) return null
  const parsed = Number(trimmed.replace(/,/g, ''))
  return Number.isFinite(parsed) ? parsed : null
}

function show(value: number | null): string {
  return value === null || value === undefined ? '' : String(value)
}

interface Props {
  fields: InvoiceFields
  onChange: (next: InvoiceFields) => void
  disabled?: boolean
}

export function FieldEditor({ fields, onChange, disabled }: Props) {
  const setText = (key: keyof InvoiceFields) => (raw: string) =>
    onChange({ ...fields, [key]: raw.trim() === '' ? null : raw })

  const setNumber = (key: keyof InvoiceFields) => (raw: string) =>
    onChange({ ...fields, [key]: toNumber(raw) })

  const setItem = (index: number, patch: Partial<LineItem>) => {
    const items = fields.line_items.map((item, i) =>
      i === index ? { ...item, ...patch } : item,
    )
    onChange({ ...fields, line_items: items })
  }

  const addRow = () =>
    onChange({
      ...fields,
      line_items: [
        ...fields.line_items,
        // 번호는 비워 둔다. 문서에 적힌 번호가 아니므로 엔진이 알아서 매긴다.
        { position: null, description: '', quantity: null, unit_price: null, amount: null, tax: null },
      ],
    })

  const removeRow = (index: number) =>
    onChange({ ...fields, line_items: fields.line_items.filter((_, i) => i !== index) })

  // id 는 검증 오류 쪽(field-${key})과 맞춰 둔다 -- 오류를 클릭하면 여기로 스크롤한다.
  const text = (label: string, key: keyof InvoiceFields) => (
    <label className="field" id={`field-${key}`}>
      <span>{label}</span>
      <input
        value={(fields[key] as string | null) ?? ''}
        onChange={(e) => setText(key)(e.target.value)}
        disabled={disabled}
      />
    </label>
  )

  const num = (label: string, key: keyof InvoiceFields) => (
    <label className="field" id={`field-${key}`}>
      <span>{label}</span>
      <input
        className="num"
        inputMode="decimal"
        value={show(fields[key] as number | null)}
        onChange={(e) => setNumber(key)(e.target.value)}
        placeholder="비어 있음"
        disabled={disabled}
      />
    </label>
  )

  return (
    <>
      <label className="field" style={{ maxWidth: 220 }}>
        <span>문서 유형</span>
        <select
          value={fields.doc_type}
          onChange={(e) => onChange({ ...fields, doc_type: e.target.value as InvoiceFields['doc_type'] })}
          disabled={disabled}
        >
          {DOC_TYPES.map((t) => (
            <option key={t.value} value={t.value}>{t.label}</option>
          ))}
        </select>
      </label>

      <div className="grid">
        {text(fields.doc_type === 'RECEIPT' ? '영수증 번호' : '송장 번호', 'invoice_number')}
        {text('발행일 (YYYY-MM-DD)', 'issue_date')}
        {text('지급 기한 (YYYY-MM-DD)', 'due_date')}
        {text('공급자명', 'vendor_name')}
        {text('수신자명', 'buyer_name')}
        {text('발주 번호', 'po_number')}
      </div>

      {/* id 는 행 단위가 아닌 품목 오류(예: 품목 합계 vs 공급가액)가 걸리는 자리다. */}
      <h3 id="field-line_items" style={{ fontSize: '.9rem', margin: '14px 0 6px' }}>품목</h3>
      <table>
        <thead>
          <tr>
            <th style={{ width: 56 }} className="num">번호</th>
            <th>품목</th>
            <th className="num" style={{ width: 90 }}>수량</th>
            <th className="num" style={{ width: 110 }}>단가</th>
            <th className="num" style={{ width: 100 }}>세액</th>
            <th className="num" style={{ width: 120 }}>금액</th>
            <th style={{ width: 44 }} />
          </tr>
        </thead>
        <tbody>
          {fields.line_items.map((item, index) => (
            // 검증 오류는 문서에 적힌 품목 번호(position)로 행을 가리킨다. 없으면
            // 표에서의 순번(1부터)을 쓴다 -- validator.rule_check 와 같은 규칙이다.
            <tr key={index} id={`field-line_items-${item.position ?? index + 1}`}>
              <td className="num muted">{item.position ?? ''}</td>
              <td>
                <input
                  value={item.description}
                  onChange={(e) => setItem(index, { description: e.target.value })}
                  disabled={disabled}
                />
              </td>
              {(['quantity', 'unit_price', 'tax', 'amount'] as const).map((key) => (
                <td key={key}>
                  <input
                    className="num"
                    inputMode="decimal"
                    value={show(item[key])}
                    onChange={(e) => setItem(index, { [key]: toNumber(e.target.value) })}
                    disabled={disabled}
                  />
                </td>
              ))}
              <td>
                <button
                  className="btn danger"
                  style={{ padding: '4px 8px' }}
                  onClick={() => removeRow(index)}
                  disabled={disabled}
                  title="이 행 삭제"
                >
                  ×
                </button>
              </td>
            </tr>
          ))}
          {fields.line_items.length === 0 && (
            <tr><td colSpan={7} className="muted small">품목이 없습니다.</td></tr>
          )}
        </tbody>
      </table>
      <div className="row" style={{ marginTop: 8 }}>
        <button className="btn" onClick={addRow} disabled={disabled}>행 추가</button>
      </div>

      <h3 style={{ fontSize: '.9rem', margin: '18px 0 6px' }}>합계</h3>
      <div className="grid">
        {text('통화', 'currency')}
        {num('공급가액', 'subtotal')}
        {num('세액', 'tax')}
        {num('배송비', 'shipping')}
        {num('총 청구액', 'total_amount')}
      </div>
    </>
  )
}
