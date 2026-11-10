import { useSynthetic } from "../api/queries";
import { t } from "../i18n";

/** Says, on every page, when the data shown is synthetic example data rather than local fixtures. */
export function SyntheticBanner() {
  if (!useSynthetic().data) return null;
  return (
    <div className="synthetic-banner" role="status">
      <strong>{t.synthetic.banner}</strong> <span>{t.synthetic.howto}</span>
    </div>
  );
}
