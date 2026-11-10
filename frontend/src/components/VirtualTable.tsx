// A TanStack Table rendered with row virtualization, for result tables of thousands of rows (screen rejects, orders).
import { flexRender, getCoreRowModel, useReactTable, type ColumnDef } from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useRef } from "react";

interface Props<T> {
  rows: T[];
  columns: ColumnDef<T, unknown>[];
  rowKey: (row: T, index: number) => string;
  onRow?: (row: T) => void;
  selected?: string | null;
  rowClass?: (row: T) => string | undefined;
  height?: number;
  label: string;
}

/** Fixed row height (cells do not wrap, .vtbl), so the spacer rows stay exact. */
const ROW = 36;

export function VirtualTable<T>({ rows, columns, rowKey, onRow, selected, rowClass, height = 520, label }: Props<T>) {
  const table = useReactTable({ data: rows, columns, getCoreRowModel: getCoreRowModel(), getRowId: (r, i) => rowKey(r, i) });
  const scroller = useRef<HTMLDivElement>(null);
  const model = table.getRowModel().rows;
  const virtual = useVirtualizer({ count: model.length, getScrollElement: () => scroller.current, estimateSize: () => ROW, overscan: 12 });
  const items = virtual.getVirtualItems();
  const top = items[0]?.start ?? 0;
  const bottom = virtual.getTotalSize() - (items[items.length - 1]?.end ?? 0);
  const span = table.getVisibleLeafColumns().length;
  return (
    <div className="tbl-wrap" ref={scroller} style={{ maxHeight: height }}>
      <table className="tbl vtbl" aria-label={label} aria-rowcount={model.length}>
        <thead>
          {table.getHeaderGroups().map((g) => (
            <tr key={g.id}>
              {g.headers.map((h) => (
                <th key={h.id} className={(h.column.columnDef.meta as { className?: string } | undefined)?.className}>
                  {h.isPlaceholder ? null : flexRender(h.column.columnDef.header, h.getContext())}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {top > 0 && (
            <tr aria-hidden>
              <td colSpan={span} style={{ height: top, padding: 0, border: 0 }} />
            </tr>
          )}
          {items.map((v) => {
            const row = model[v.index]!;
            const classes = [onRow ? "clickable" : "", rowClass?.(row.original) ?? ""].filter(Boolean).join(" ");
            return (
              <tr
                key={row.id}
                data-index={v.index}
                className={classes || undefined}
                aria-selected={selected === row.id || undefined}
                onClick={onRow ? () => onRow(row.original) : undefined}
              >
                {row.getVisibleCells().map((c) => (
                  <td key={c.id} className={(c.column.columnDef.meta as { className?: string } | undefined)?.className}>
                    {flexRender(c.column.columnDef.cell, c.getContext())}
                  </td>
                ))}
              </tr>
            );
          })}
          {bottom > 0 && (
            <tr aria-hidden>
              <td colSpan={span} style={{ height: bottom, padding: 0, border: 0 }} />
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
