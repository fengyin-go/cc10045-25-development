import { createRouter, createWebHistory } from 'vue-router'

import AttributionHandoverView from '@/views/AttributionHandoverView.vue'
import CalibrationDiffView from '@/views/CalibrationDiffView.vue'
import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    { path: '/calibration-diff', name: 'calibration-diff', component: CalibrationDiffView },
    {
      path: '/attribution-handovers',
      name: 'attribution-handovers',
      component: AttributionHandoverView,
    },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
