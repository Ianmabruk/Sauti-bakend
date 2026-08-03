import { Route, Routes } from 'react-router-dom'
import { Layout } from '../components/layout/Layout'
import { ProtectedRoute } from './ProtectedRoute'
import { AccountPage } from '../pages/account/AccountPage'
import { AdminPage } from '../pages/admin/AdminPage'
import { AuthShell } from '../pages/auth/AuthShell'
import { ForgotPasswordPage } from '../pages/auth/ForgotPasswordPage'
import { LoginPage } from '../pages/auth/LoginPage'
import { RegisterPage } from '../pages/auth/RegisterPage'
import { ResetPasswordPage } from '../pages/auth/ResetPasswordPage'
import { AboutPage } from '../pages/public/AboutPage'
import { CartPage } from '../pages/account/CartPage'
import { WishlistPage } from '../pages/account/WishlistPage'
import { ChatPage } from '../pages/public/ChatPage'
import { HomePage } from '../pages/public/HomePage'
import { ProductDetailPage } from '../pages/public/ProductDetailPage'
import { PortfolioPage } from '../pages/public/PortfolioPage'
import { ProjectsPage } from '../pages/public/ProjectsPage'
import { ShopPage } from '../pages/public/ShopPage'
import { VirtualDesignPage } from '../pages/public/VirtualDesignPage'
import { NotFoundPage } from '../pages/public/NotFoundPage'

export const AppRouter = () => {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<HomePage />} />
        <Route path="/shop" element={<ShopPage />} />
        <Route path="/shop/:id" element={<ProductDetailPage />} />
        <Route path="/portfolio" element={<PortfolioPage />} />
        <Route path="/projects" element={<ProjectsPage />} />
        <Route path="/about" element={<AboutPage />} />
        <Route path="/virtual-design" element={<VirtualDesignPage />} />
        <Route path="/virtual-interior-design" element={<VirtualDesignPage />} />
        <Route path="/chat" element={<ChatPage />} />

        <Route element={<ProtectedRoute />}>
          <Route path="/account" element={<AccountPage />} />
        </Route>

        <Route path="/wishlist" element={<WishlistPage />} />
        <Route path="/cart" element={<CartPage />} />

        <Route element={<ProtectedRoute adminOnly />}>
          <Route path="/admin" element={<AdminPage />} />
        </Route>
      </Route>

      <Route element={<AuthShell />}>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/reset-password/:token" element={<ResetPasswordPage />} />
      </Route>

      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  )
}
