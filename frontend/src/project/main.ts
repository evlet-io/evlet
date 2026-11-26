// Entry of the project portal (project.html), served by the project endpoint at / (docs/project-endpoint.md).
import '../evlet/ui/styles.css';
import './portal.css';
import { start } from './portal';

start(document.getElementById('evlet-project')!);
