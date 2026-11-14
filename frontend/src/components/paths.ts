// Folder paths as the backend gives them (POSIX "/home/a/b" or Windows "C:\Users\a"), split for the folder browser's
// breadcrumbs and joined for a new folder's name. Only strings: the backend lists, checks and makes the folders.

export interface Crumb {
  label: string;
  path: string;
}

const windows = (p: string) => /^[A-Za-z]:[\\/]?/.test(p) || (p.includes("\\") && !p.includes("/"));

/** The path's folders from its root (or from the place it lies in, named as the place) down to itself. */
export function crumbs(path: string, places: readonly Crumb[] = []): Crumb[] {
  const win = windows(path);
  const sep = win ? "\\" : "/";
  const parts = path.split(/[\\/]/).filter(Boolean);
  const all: Crumb[] = win
    ? parts.map((p, i) => ({ label: i === 0 ? `${p}${sep}` : p, path: i === 0 ? `${p}${sep}` : parts.slice(0, i + 1).join(sep) }))
    : [{ label: "/", path: "/" }, ...parts.map((p, i) => ({ label: p, path: `/${parts.slice(0, i + 1).join("/")}` }))];
  // The deepest place the path lies in replaces the crumbs above it, e.g. 主目录 for /home/me.
  const norm = (p: string) => (p.length > 1 ? p.replace(/[\\/]+$/, "") : p);
  let at = -1;
  let name = "";
  places.forEach((pl) => {
    const i = all.findIndex((c) => norm(c.path) === norm(pl.path));
    if (i > at && i >= 0) {
      at = i;
      name = pl.label;
    }
  });
  return at < 0 ? all : [{ label: name, path: all[at]!.path }, ...all.slice(at + 1)];
}

/** dir/name, with the separator dir uses. */
export function join(dir: string, name: string): string {
  const sep = windows(dir) ? "\\" : "/";
  return dir.endsWith("/") || dir.endsWith("\\") ? `${dir}${name}` : `${dir}${sep}${name}`;
}
