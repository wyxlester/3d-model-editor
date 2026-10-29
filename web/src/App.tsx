import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { Home } from './screens/Home';
import { ProjectPicker } from './screens/ProjectPicker';
import { ShipResearch } from './screens/ShipResearch';
import { ShipBuild } from './screens/ShipBuild';
import { CrewWorkshop } from './screens/CrewWorkshop';
import { Moves } from './screens/Moves';
import { Assembly } from './screens/Assembly';
import { Review } from './screens/Review';
import { VariantReview } from './screens/VariantReview';
import { JobCenterProvider } from './components/JobCenter';

export function App() {
  return (
    <BrowserRouter>
      <JobCenterProvider>
      <div className="app">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/ship" element={<ProjectPicker kind="ship" />} />
          <Route path="/ship/:id/research" element={<ShipResearch />} />
          <Route path="/ship/:id/build" element={<ShipBuild />} />
          <Route path="/crew" element={<ProjectPicker kind="crew" />} />
          <Route path="/crew/:id" element={<CrewWorkshop />} />
          <Route path="/crew/:id/moves" element={<Moves />} />
          <Route path="/assembly" element={<Assembly />} />
          <Route path="/assembly/:sceneId" element={<Assembly />} />
          <Route path="/review" element={<Review />} />
          <Route path="/review/:sceneId" element={<Review />} />
          <Route path="/variant/:id" element={<VariantReview />} />
          <Route path="*" element={<Home />} />
        </Routes>
      </div>
      </JobCenterProvider>
    </BrowserRouter>
  );
}
